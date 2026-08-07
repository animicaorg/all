import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate } from '@/lib/api';
import { rateLimit } from '@/lib/apikey';
import { requireAdmin } from '@/lib/adminAuth';
import { sendMailSafe } from '@/lib/hireMail';
import { HIRE_NOTIFY_EMAIL } from '@/lib/hire';
import { trackBillingEvent } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Enterprise ("contact sales") intake for the Python Cloud commercial catalog. Enterprise has
// no PayPal plan on purpose — custom quotas, custom price — so the pricing page's Enterprise
// CTA lands here instead of a checkout. The operator's inbox address stays SERVER-SIDE
// (HIRE_NOTIFY_EMAIL): the frontend only ever POSTs this endpoint, so no private address can
// leak into page source.

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip') || 'unknown';
}

// Pragmatic RFC-5322 subset: something@domain.tld, no whitespace, sane length. Strict enough
// to refuse garbage; loose enough to never bounce a real corporate address.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function str(v: unknown, max: number): string {
  return typeof v === 'string' ? v.trim().slice(0, max) : '';
}

const INQUIRY_STATUSES = ['NEW', 'CONTACTED', 'QUALIFIED', 'WON', 'LOST'];

// POST /api/cloud/v1/enterprise — public (a prospect may not have a wallet yet). Server-side
// validation + two rate-limit layers: an in-process per-IP limiter for bursts, and a DB
// flood guard (same-source daily cap) that survives process restarts.
export async function POST(req: NextRequest) {
  try {
    const ip = clientIp(req);
    const rl = rateLimit('enterprise:ip:' + ip, Number(process.env.CLOUD_ENTERPRISE_RATE_PER_MIN ?? 3));
    if (!rl.ok) throw new ApiError(429, 'rate_limited', `retry after ${rl.retryAfter}s`);

    let body: any = {};
    try { body = await req.json(); } catch {}

    const name = str(body?.name, 120);
    const company = str(body?.company, 160);
    const email = str(body?.email, 200);
    if (!name) throw new ApiError(400, 'invalid', 'name is required');
    if (!company) throw new ApiError(400, 'invalid', 'company is required');
    if (!EMAIL_RE.test(email)) throw new ApiError(400, 'invalid', 'a valid work email is required');

    const workload = str(body?.workload, 2000);
    const aiNeeds = str(body?.aiNeeds, 1000);
    const computeNeeds = str(body?.computeNeeds, 1000);
    const monthlyExecs = str(body?.monthlyExecs, 100);
    const message = str(body?.message, 4000);
    const dedicated = body?.dedicated === true;

    // Signed-in prospects get linked to their account (helps sales see real usage); the
    // form still works logged out. authenticate() may throw 429 for a rate-limited key —
    // that must not break an anonymous-capable form.
    let accountId: string | null = null;
    try {
      const ctx = await authenticate(req);
      accountId = ctx?.accountId ?? null;
    } catch {}

    // DB flood guard: one prospect legitimately sends one or two inquiries, not dozens.
    // Keyed by email — the durable identity on an unauthenticated form.
    const since = new Date(Date.now() - 24 * 3600 * 1000);
    const recent = await prisma.enterpriseInquiry.count({
      where: { email, createdAt: { gte: since } },
    });
    if (recent >= 3) {
      throw new ApiError(429, 'rate_limited', 'we already have your inquiry — our team will reach out within one business day');
    }

    const inquiry = await prisma.enterpriseInquiry.create({
      data: { name, company, email, workload, aiNeeds, computeNeeds, monthlyExecs, dedicated, message, accountId },
    });
    trackBillingEvent('enterprise_inquiry', { accountId: accountId ?? undefined, planKey: 'enterprise' }).catch(() => {});

    // Operator notification through the existing transactional mailer (fail-soft: a mail
    // outage must never lose the inquiry — the DB row is the source of truth and the admin
    // GET below lists it either way).
    sendMailSafe({
      to: HIRE_NOTIFY_EMAIL,
      subject: `Animica Cloud enterprise inquiry: ${company}`,
      replyTo: email,
      text:
        `New enterprise inquiry for Animica Python Cloud.\n\n` +
        `Inquiry: ${inquiry.id}\nName: ${name}\nCompany: ${company}\nEmail: ${email}\n` +
        `Account: ${accountId ?? 'not signed in'}\n` +
        `Dedicated infra: ${dedicated ? 'yes' : 'no'}\n` +
        (monthlyExecs ? `Monthly executions: ${monthlyExecs}\n` : '') +
        (workload ? `\nWorkload:\n${workload}\n` : '') +
        (aiNeeds ? `\nAI needs:\n${aiNeeds}\n` : '') +
        (computeNeeds ? `\nCompute needs:\n${computeNeeds}\n` : '') +
        (message ? `\nMessage:\n${message}\n` : '') +
        `\nAdmin: https://animica.dev/admin/billing`,
    }).catch(() => {});

    return ok({
      id: inquiry.id,
      status: inquiry.status,
      message: 'Thanks — our team will reach out within one business day.',
    });
  } catch (e) {
    return err(e);
  }
}

// GET /api/cloud/v1/enterprise?status=&take=&skip= — the sales queue. Admin only.
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const sp = req.nextUrl.searchParams;
    const status = (sp.get('status') ?? '').trim();
    const take = Math.min(200, Math.max(1, Number(sp.get('take')) || 50));
    const skip = Math.max(0, Math.trunc(Number(sp.get('skip')) || 0));
    if (status && !INQUIRY_STATUSES.includes(status)) {
      throw new ApiError(400, 'bad_request', `status must be one of ${INQUIRY_STATUSES.join(' | ')}`);
    }
    const where = status ? { status } : {};
    const [rows, count] = await Promise.all([
      prisma.enterpriseInquiry.findMany({ where, orderBy: { createdAt: 'desc' }, take, skip }),
      prisma.enterpriseInquiry.count({ where }),
    ]);
    return ok({ rows, totals: { count }, take, skip });
  } catch (e) {
    return err(e);
  }
}
