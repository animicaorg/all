import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { sendMailSafe } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/site-feedback — the "Feedback" button on animica.dev and /cli. Emails the
// operator.
//
// NOT at /api/mkt/v1/feedback: that URL is already taken by the AI chat's thumbs-up /
// thumbs-down and DPO preference capture, which writes to prisma.feedback and forwards
// consented preference pairs to the ENA training coordinator. Two unrelated features, two
// paths.
//
// Deliberately UNAUTHENTICATED: the point is to hear from someone who just tried the CLI and
// has not signed in, and gating feedback behind a post-quantum wallet extension would collect
// nothing. That makes it a public endpoint that causes an email to be sent to the operator's
// own inbox, i.e. an abuse vector aimed at them, so everything below exists to make it
// expensive to misuse and impossible to use as a header-injection or relay primitive.

const TO = process.env.FEEDBACK_NOTIFY_EMAIL || process.env.HIRE_NOTIFY_EMAIL || '';

const MIN_LEN = 5;
const MAX_LEN = 4000;
const MAX_EMAIL = 254;          // RFC 5321 practical maximum
const MAX_BODY_BYTES = 16_000;  // read cap before parsing

// Per-IP and global windows. In-process, so it resets on redeploy and is per-instance — enough
// for a single Next server, and honest about what it is: friction, not a guarantee. nginx carries
// its own limit_req in front of this route as the real ceiling.
const PER_IP_MAX = 3;
const PER_IP_WINDOW_MS = 10 * 60_000;
const GLOBAL_MAX = 60;
const GLOBAL_WINDOW_MS = 60 * 60_000;

const hits = new Map<string, number[]>();
let globalHits: number[] = [];

function tooMany(ip: string): boolean {
  const now = Date.now();
  globalHits = globalHits.filter((t) => now - t < GLOBAL_WINDOW_MS);
  if (globalHits.length >= GLOBAL_MAX) return true;
  const mine = (hits.get(ip) || []).filter((t) => now - t < PER_IP_WINDOW_MS);
  if (mine.length >= PER_IP_MAX) {
    hits.set(ip, mine);
    return true;
  }
  mine.push(now);
  hits.set(ip, mine);
  globalHits.push(now);
  // Keep the map from growing without bound on a long-lived process.
  if (hits.size > 5000) {
    for (const [k, v] of hits) if (!v.some((t) => now - t < PER_IP_WINDOW_MS)) hits.delete(k);
  }
  return false;
}

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for') || '';
  return (xff.split(',')[0] || req.headers.get('x-real-ip') || 'unknown').trim();
}

// Anything that reaches a mail HEADER must not be able to contain a newline, or it can inject
// extra headers (a Bcc, a different To) into the message. Body text is safe to keep verbatim.
function headerSafe(s: string, max: number): string {
  return s.replace(/[\r\n\t]+/g, ' ').replace(/\s{2,}/g, ' ').trim().slice(0, max);
}

const EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$/;

export async function POST(req: NextRequest) {
  try {
    if (!TO) {
      // Never claim a send when there is nowhere to send it.
      throw new ApiError(503, 'feedback_unconfigured', 'feedback email is not configured on this server');
    }

    const raw = await req.text();
    if (raw.length > MAX_BODY_BYTES) throw new ApiError(413, 'too_large', 'feedback is too long');
    let body: any = {};
    try { body = JSON.parse(raw || '{}'); } catch { throw new ApiError(400, 'invalid', 'expected JSON'); }

    // Honeypot: a field hidden from humans by CSS. Bots fill every input they find. Answer 200
    // so the bot believes it succeeded and does not retry with a different shape.
    if (String(body?.website || '').trim()) return ok({ ok: true });

    const message = String(body?.message ?? '').trim();
    if (message.length < MIN_LEN) throw new ApiError(400, 'invalid', 'please write a little more');
    if (message.length > MAX_LEN) throw new ApiError(400, 'invalid', `keep it under ${MAX_LEN} characters`);

    const emailRaw = headerSafe(String(body?.email ?? ''), MAX_EMAIL);
    if (emailRaw && !EMAIL_RE.test(emailRaw)) {
      throw new ApiError(400, 'invalid', 'that email address does not look right');
    }

    const ip = clientIp(req);
    if (tooMany(ip)) {
      throw new ApiError(429, 'rate_limited', 'thanks — you have sent a few already. Try again later.');
    }

    const page = headerSafe(String(body?.page ?? ''), 200);
    const ua = headerSafe(req.headers.get('user-agent') || '', 200);
    const subject = `Animica feedback${emailRaw ? ` from ${emailRaw}` : ''}`;

    const facts = [
      ['From', emailRaw || '(not given)'],
      ['Page', page || '(unknown)'],
      ['IP', ip],
      ['User agent', ua],
      ['Received', new Date().toISOString()],
    ] as Array<[string, string]>;

    const esc = (s: string) =>
      s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    const sent = await sendMailSafe({
      to: TO,
      subject,
      // replyTo only when the address parsed — otherwise replying would bounce, and a
      // half-valid header is worse than none.
      replyTo: emailRaw || undefined,
      text:
        `${message}\n\n---\n` + facts.map(([k, v]) => `${k}: ${v}`).join('\n'),
      html:
        `<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:560px;color:#1c1e26">` +
        `<h2 style="letter-spacing:-.02em">Feedback from animica.dev</h2>` +
        `<pre style="white-space:pre-wrap;font-family:inherit;background:#f4f5f9;padding:12px;border-radius:8px">${esc(message)}</pre>` +
        `<table style="border-collapse:collapse;font-size:13px;margin-top:14px">` +
        facts
          .map(
            ([k, v]) =>
              `<tr><td style="padding:3px 14px 3px 0;color:#5a5f73;white-space:nowrap;vertical-align:top">${esc(k)}</td>` +
              `<td style="padding:3px 0">${esc(v)}</td></tr>`,
          )
          .join('') +
        `</table></div>`,
    });

    if (!sent) {
      // sendMailSafe is fail-soft by design for checkout receipts. Here the send IS the
      // feature, so a failure must be reported rather than swallowed into a false "thanks!".
      throw new ApiError(502, 'send_failed', 'could not send that just now — please try again shortly');
    }

    return ok({ ok: true });
  } catch (e) {
    return err(e);
  }
}
