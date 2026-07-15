import { NextRequest, NextResponse } from 'next/server';
import { requireInternal } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// INTERNAL. The engine calls this each cycle so the console can show liveness + posture.
export async function POST(req: NextRequest) {
  const bad = requireInternal(req);
  if (bad) return bad;
  let body: any = {};
  try { body = await req.json(); } catch {}

  const data = {
    running: true,
    dryRun: body?.dryRun !== false,           // default to dry-run unless engine explicitly says live
    lastHeartbeat: new Date(),
    lastCycleJson: JSON.stringify(body?.cycle ?? {}).slice(0, 8000),
  };
  await prisma.animalEngineState.upsert({
    where: { id: 'animal' },
    create: { id: 'animal', ...data },
    update: data,
  });
  const st = await prisma.animalEngineState.findUnique({ where: { id: 'animal' } });
  return NextResponse.json({ ok: true, paused: st?.paused ?? false });
}
