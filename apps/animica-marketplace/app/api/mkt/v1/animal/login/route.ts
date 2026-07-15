import { NextRequest, NextResponse } from 'next/server';
import { checkThrottle, recordAttempt, verifyCredentials, setConsoleCookie } from '@/lib/animalAuth';
import { clientIp } from '@/lib/animalApi';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Operator login for the Animica Animal console. Rate-limited: a per-IP + global lockout throttles
// online password guessing. Responses are deliberately uniform ("invalid credentials") so the
// username is not an oracle.
export async function POST(req: NextRequest) {
  const ip = clientIp(req);
  const t = await checkThrottle(ip);
  if (!t.allowed) {
    return NextResponse.json(
      { error: 'locked', message: `Too many attempts. Try again in ${t.retryAfter}s.`, retryAfter: t.retryAfter },
      { status: 429, headers: { 'retry-after': String(t.retryAfter) } },
    );
  }
  let body: any = {};
  try { body = await req.json(); } catch {}
  const user = typeof body?.user === 'string' ? body.user : '';
  const password = typeof body?.password === 'string' ? body.password : '';

  const ok = verifyCredentials(user, password);
  await recordAttempt(ip, ok);
  if (!ok) {
    return NextResponse.json({ error: 'invalid', message: 'Invalid credentials.' }, { status: 401 });
  }
  await setConsoleCookie();
  return NextResponse.json({ ok: true });
}
