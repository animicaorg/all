import { NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Engine liveness + posture + rollups for the console header.
export async function GET() {
  const unauth = await requireConsole();
  if (unauth) return unauth;

  const st = await prisma.animalEngineState.findUnique({ where: { id: 'animal' } });
  const now = Date.now();
  const alive = st?.lastHeartbeat ? now - st.lastHeartbeat.getTime() < 120_000 : false;

  const [connected, posted, dryRun] = await Promise.all([
    prisma.socialConnection.count({ where: { status: 'CONNECTED' } }),
    prisma.animalPost.count({ where: { status: 'POSTED' } }),
    prisma.animalPost.count({ where: { status: 'DRY_RUN' } }),
  ]);

  return NextResponse.json({
    engine: {
      running: Boolean(st?.running) && alive,
      alive,
      dryRun: st?.dryRun ?? true,
      paused: st?.paused ?? false,
      lastHeartbeat: st?.lastHeartbeat ?? null,
      lastCycle: st ? safeParse(st.lastCycleJson) : {},
    },
    counts: { connected, posted, previews: dryRun },
  });
}

function safeParse(s: string) { try { return JSON.parse(s); } catch { return {}; } }
