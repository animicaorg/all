import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanEmail } from '@/lib/hire';
import { checkHireThrottle, recordHireAttempt, verifyCustomerLogin, issueCustomerSession } from '@/lib/hireAuth';
import { clientIp } from '@/lib/animalApi';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  try {
    const ip = clientIp(req);
    const t = await checkHireThrottle(ip);
    if (!t.allowed) throw new ApiError(429, 'locked', `Too many attempts. Try again in ${t.retryAfter}s.`);

    let body: any = {};
    try { body = await req.json(); } catch {}
    const email = cleanEmail(body?.email);
    const password = typeof body?.password === 'string' ? body.password : '';

    const cust = await verifyCustomerLogin(email, password);
    await recordHireAttempt(ip, !!cust);
    if (!cust) throw new ApiError(401, 'invalid', 'Invalid email or password.');

    await prisma.hireCustomer.update({ where: { id: cust.id }, data: { lastLoginAt: new Date() } });
    await issueCustomerSession(cust.id, cust.sessionEpoch);
    return ok({ customer: { email: cust.email, name: cust.name, company: cust.company, discord: cust.discord } });
  } catch (e) {
    return err(e);
  }
}
