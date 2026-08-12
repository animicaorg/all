import { NextRequest, NextResponse } from 'next/server';
import { resolveMiner, claimJob } from '@/lib/mediaQueue';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// A registered miner long-polls for the next job it can serve. Also a heartbeat. Returns
// the claimed job (incl. the private input, handed only to this authenticated miner) or 204.
// Optional body { device?, load? } updates live stats.
export async function POST(req: NextRequest) {
  const miner = await resolveMiner(bearer(req));
  if (!miner) return NextResponse.json({ error: 'unregistered_miner' }, { status: 401 });

  let body: any = {};
  try { body = await req.json(); } catch { /* empty body ok */ }
  if (typeof body.load === 'number' || typeof body.device === 'string') {
    await prisma.mediaMiner.update({
      where: { id: miner.id },
      data: {
        online: true,
        lastSeenAt: new Date(),
        ...(typeof body.load === 'number' ? { load: Math.max(0, Math.min(body.load, 1)) } : {}),
        ...(typeof body.device === 'string' ? { device: body.device.slice(0, 24) } : {}),
      },
    }).catch(() => {});
  }

  const job = await claimJob(miner.id, miner.capabilities);
  if (!job) return new NextResponse(null, { status: 204 });

  return NextResponse.json({
    job: {
      id: job.id,
      kind: job.kind,
      prompt: job.prompt,
      params: job.params,
      images: job.inputB64 ? safeParseImages(job.inputB64) : [],
      // 9.0.0 studio kinds: gateway-relative URLs for the job's input files (fetched with
      // this miner's bearer token; render_assemble gets the chunk artifact URLs here).
      input_urls: job.input_urls,
      is_private: job.isPrivate,
      attempts: job.attempts,
    },
  });
}

function safeParseImages(s: string): string[] {
  try { const a = JSON.parse(s); return Array.isArray(a) ? a : []; } catch { return []; }
}
