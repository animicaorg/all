import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { currentCustomer, clearCustomerCookie } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Logout-all: bump the sessionEpoch so every outstanding token dies, then clear the cookie.
export async function POST(_req: NextRequest) {
  try {
    const cust = await currentCustomer();
    if (cust) {
      await prisma.hireCustomer.update({
        where: { id: cust.id },
        data: { sessionEpoch: { increment: 1 } },
      });
    }
    clearCustomerCookie();
    return ok({ ok: true });
  } catch (e) {
    return err(e);
  }
}
