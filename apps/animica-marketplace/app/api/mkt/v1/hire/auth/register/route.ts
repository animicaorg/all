import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanEmail, cleanPassword, cleanStr } from '@/lib/hire';
import { checkHireThrottle, recordHireAttempt, hashPassword, issueCustomerSession } from '@/lib/hireAuth';
import { clientIp } from '@/lib/animalApi';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Create a hire-customer account (email + password) and sign in. Registration shares the login
// throttle so it can't be used to hammer scrypt or enumerate emails without cost.
export async function POST(req: NextRequest) {
  try {
    const ip = clientIp(req);
    const t = await checkHireThrottle(ip);
    if (!t.allowed) throw new ApiError(429, 'locked', `Too many attempts. Try again in ${t.retryAfter}s.`);

    let body: any = {};
    try { body = await req.json(); } catch {}
    const email = cleanEmail(body?.email);
    const password = cleanPassword(body?.password);
    const name = cleanStr(body?.name, 120, 'name', true)!;
    const company = cleanStr(body?.company, 160, 'company');
    const discord = cleanStr(body?.discord, 80, 'discord');

    const existing = await prisma.hireCustomer.findUnique({ where: { email } });
    if (existing) {
      await recordHireAttempt(ip, false);
      throw new ApiError(409, 'exists', 'An account with this email already exists — sign in instead.');
    }

    let cust;
    try {
      cust = await prisma.hireCustomer.create({
        data: { email, passwordHash: hashPassword(password), name, company, discord, lastLoginAt: new Date() },
      });
    } catch (e: any) {
      // Concurrent signups with the same address race past the findUnique above.
      if (e?.code === 'P2002') {
        await recordHireAttempt(ip, false);
        throw new ApiError(409, 'exists', 'An account with this email already exists — sign in instead.');
      }
      throw e;
    }
    await recordHireAttempt(ip, true);
    await issueCustomerSession(cust.id, cust.sessionEpoch);
    return ok({ customer: { email: cust.email, name: cust.name, company: cust.company, discord: cust.discord } });
  } catch (e) {
    return err(e);
  }
}
