import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL (Bearer + edge-denied): the stream worker heartbeats its live status here so
// the console + homepage can show "● LIVE" with a watch link and viewer count.
export async function POST(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;
  let b: any = {};
  try { b = await req.json(); } catch { /* ignore */ }
  const live = {
    live: !!b?.live,
    watchUrl: String(b?.watchUrl || '').slice(0, 300),
    viewers: Number(b?.viewers) || 0,
    uptime: Number(b?.uptime) || 0,
    character: String(b?.character || '').slice(0, 60),
    at: new Date().toISOString(),
  };
  await prisma.animalEngineState.upsert({
    where: { id: 'animal' },
    update: { liveJson: JSON.stringify(live) },
    create: { id: 'animal', liveJson: JSON.stringify(live) },
  });
  return NextResponse.json({ ok: true });
}
