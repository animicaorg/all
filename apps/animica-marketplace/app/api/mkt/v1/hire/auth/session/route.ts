import { NextRequest } from 'next/server';
import { ok, err } from '@/lib/api';
import { currentCustomer } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_req: NextRequest) {
  try {
    const cust = await currentCustomer();
    return ok({
      customer: cust
        ? { email: cust.email, name: cust.name, company: cust.company, discord: cust.discord }
        : null,
    });
  } catch (e) {
    return err(e);
  }
}
