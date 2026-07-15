import { NextRequest, NextResponse } from 'next/server';
import { requireConsole } from '@/lib/animalApi';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Operator kill-switch / posture. `paused` stops the engine from posting (it honors this each
// cycle). The console CANNOT enable a live send here — going live requires the engine's own
// GROWTH_DRY_RUN=0 + GROWTH_ALLOW_LIVE_POST=1 flags on the box. This route can only pause, or
// request dry-run; it can never widen posting authority from the browser.
export async function POST(req: NextRequest) {
  const unauth = await requireConsole();
  if (unauth) return unauth;
  let body: any = {};
  try { body = await req.json(); } catch {}

  const data: any = {};
  if (typeof body?.paused === 'boolean') data.paused = body.paused;
  // forceDryRun is the operator override the ENGINE actually reads (via internal/state) and honors
  // each cycle. Setting it true forces previews-only regardless of the engine's env flags; clearing
  // it does NOT grant live posting (that still requires the engine's own double-gate on the box).
  if (typeof body?.forceDryRun === 'boolean') data.forceDryRun = body.forceDryRun;
  if (Object.keys(data).length === 0) return NextResponse.json({ error: 'nothing to set' }, { status: 400 });

  const st = await prisma.animalEngineState.upsert({
    where: { id: 'animal' },
    create: { id: 'animal', ...data },
    update: data,
  });
  return NextResponse.json({ ok: true, engine: { paused: st.paused, forceDryRun: st.forceDryRun } });
}
