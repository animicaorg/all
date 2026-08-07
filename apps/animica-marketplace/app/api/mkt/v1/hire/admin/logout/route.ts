import { NextRequest, NextResponse } from 'next/server';
import { clearAdminCookie } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(_req: NextRequest) {
  clearAdminCookie();
  return NextResponse.json({ ok: true });
}
