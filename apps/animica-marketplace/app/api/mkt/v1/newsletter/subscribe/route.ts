import { NextRequest, NextResponse } from 'next/server';
import { publicOk, publicPreflight, PUBLIC_CORS } from '@/lib/api';
import { rateLimit } from '@/lib/apikey';
import { config } from '@/lib/config';
import { normalizeEmail, subscribe, emailHash, CONSENT_TEXT } from '@/lib/newsletter';
import { enqueueMail, confirmEmail } from '@/lib/mailer';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function clientIp(req: NextRequest): string {
  const xff = req.headers.get('x-forwarded-for');
  if (xff) return xff.split(',')[0].trim();
  return req.headers.get('x-real-ip') || 'unknown';
}

export function OPTIONS() { return publicPreflight(); }

// Public double-opt-in signup. Always returns the SAME neutral response (no enumeration of whether
// an address is new/existing/suppressed). Rate-limited per IP; the subscribe() cooldown bounds
// per-email reissue. On a fresh PENDING signup, enqueues a transactional confirm email.
export async function POST(req: NextRequest) {
  const ip = clientIp(req);
  const rl = rateLimit('nlsub:ip:' + ip, Number(process.env.GROWTH_SIGNUP_RATE_PER_MIN ?? 5));
  const neutral = () => publicOk({
    ok: true,
    message: "If that address isn't already subscribed, we've emailed a confirmation link. Click it to finish subscribing.",
  });
  if (!rl.ok) return neutral();

  let body: any;
  try { body = await req.json(); } catch { return neutral(); }

  const email = normalizeEmail(body?.email);
  if (!email) {
    return NextResponse.json({ ok: false, error: 'Enter a valid email address.' }, { status: 400, headers: PUBLIC_CORS });
  }

  // Optional hCaptcha (only enforced when a secret is configured).
  const capSecret = process.env.GROWTH_HCAPTCHA_SECRET;
  if (capSecret) {
    const token = typeof body?.captcha === 'string' ? body.captcha : '';
    const ok = await verifyHcaptcha(capSecret, token, ip);
    if (!ok) return NextResponse.json({ ok: false, error: 'Captcha failed — please retry.' }, { status: 400, headers: PUBLIC_CORS });
  }

  const res = await subscribe({
    email,
    source: typeof body?.source === 'string' ? body.source : 'animica.dev',
    ip,
    ua: req.headers.get('user-agent') || '',
  });

  if (res.rawToken && res.subscriberId) {
    const confirmUrl = `${config.baseUrl}/api/mkt/v1/newsletter/confirm?token=${encodeURIComponent(res.rawToken)}`;
    enqueueMail(confirmEmail(email, confirmUrl));
  }
  return neutral();
}

async function verifyHcaptcha(secret: string, token: string, ip: string): Promise<boolean> {
  if (!token) return false;
  try {
    const r = await fetch('https://hcaptcha.com/siteverify', {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ secret, response: token, remoteip: ip }),
      signal: AbortSignal.timeout(5000),
    });
    const d = await r.json();
    return !!d.success;
  } catch { return false; }
}
