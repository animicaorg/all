import { NextRequest } from 'next/server';
import { ok } from '@/lib/api';
import { isHireAdminCookie } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(_req: NextRequest) {
  return ok({ authed: isHireAdminCookie() });
}
