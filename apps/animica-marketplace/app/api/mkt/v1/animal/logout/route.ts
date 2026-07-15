import { NextResponse } from 'next/server';
import { clearConsoleCookie, bumpConsoleEpoch } from '@/lib/animalAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST() {
  // Revoke ALL outstanding console tokens (not just this browser's cookie) by rotating the epoch.
  await bumpConsoleEpoch();
  clearConsoleCookie();
  return NextResponse.json({ ok: true });
}
