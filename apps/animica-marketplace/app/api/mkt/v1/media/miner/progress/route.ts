import { NextRequest, NextResponse } from 'next/server';
import { resolveMiner, postProgress } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// 9.0.0: a miner's progress heartbeat for a long render. Stores pct/note for the
// requester's poll AND extends the job lease so a live render is never swept.
// Body: { job_id, pct, note? }
export async function POST(req: NextRequest) {
  const miner = await resolveMiner(bearer(req));
  if (!miner) return NextResponse.json({ error: 'unregistered_miner' }, { status: 401 });

  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: 'bad_json' }, { status: 400 }); }
  const jobId = String(body.job_id || '');
  if (!jobId) return NextResponse.json({ error: 'missing_job_id' }, { status: 400 });

  const r = await postProgress(miner.id, jobId, Number(body.pct), typeof body.note === 'string' ? body.note : undefined);
  if (!r.ok) {
    const status = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : 409;
    return NextResponse.json({ error: r.code }, { status });
  }
  return NextResponse.json({ ok: true });
}
