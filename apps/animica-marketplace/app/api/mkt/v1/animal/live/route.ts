import { NextResponse } from 'next/server';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// PUBLIC (no secrets): current livestream status, for the console badge + homepage
// "watch live" callout. Goes offline automatically if the worker stops heartbeating.
export async function GET() {
  const st = await prisma.animalEngineState.findUnique({ where: { id: 'animal' } });
  let parsed: any = {};
  try { parsed = JSON.parse(st?.liveJson || '{}'); } catch { /* ignore */ }
  const fresh = parsed?.at && Date.now() - Date.parse(parsed.at) <= 90_000;
  const live = fresh && parsed?.live === true
    ? { live: true, watchUrl: parsed.watchUrl || '', viewers: parsed.viewers || 0, uptime: parsed.uptime || 0, character: parsed.character || '' }
    : { live: false };
  return NextResponse.json({ live });
}
