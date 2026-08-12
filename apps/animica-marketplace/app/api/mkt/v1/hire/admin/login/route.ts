import { NextRequest, NextResponse } from 'next/server';
import { checkHireThrottle, recordHireAttempt, verifyAdminCredentials, setAdminCookie } from '@/lib/hireAuth';
import { clientIp } from '@/lib/animalApi';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Operator login for admin.animica.dev. Uniform "invalid credentials" responses; durable
// per-IP + global throttle (shared 'hire:' buckets with customer login).
export async function POST(req: NextRequest) {
  const ip = clientIp(req);
  const t = await checkHireThrottle(ip);
  if (!t.allowed) {
    return NextResponse.json(
      { error: 'locked', message: `Too many attempts. Try again in ${t.retryAfter}s.` },
      { status: 429, headers: { 'retry-after': String(t.retryAfter) } },
    );
  }
  let body: any = {};
  try { body = await req.json(); } catch {}
  const user = typeof body?.user === 'string' ? body.user : '';
  const password = typeof body?.password === 'string' ? body.password : '';

  const okCred = verifyAdminCredentials(user, password);
  await recordHireAttempt(ip, okCred);
  if (!okCred) return NextResponse.json({ error: 'invalid', message: 'Invalid credentials.' }, { status: 401 });
  setAdminCookie();
  return NextResponse.json({ ok: true });
}
