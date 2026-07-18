import { NextRequest, NextResponse } from 'next/server';
import { resolveMiner, postResult } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const MAX_RESULT = 48 * 1024 * 1024; // encoded result cap (~36MB binary)

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// A miner posts the rendered result (or a failure) for a job it claimed.
// Body: { job_id, ok, b64?, mime?, sha3?, meta?, error? }.
export async function POST(req: NextRequest) {
  const miner = await resolveMiner(bearer(req));
  if (!miner) return NextResponse.json({ error: 'unregistered_miner' }, { status: 401 });

  const raw = await req.text();
  if (raw.length > MAX_RESULT) return NextResponse.json({ error: 'result_too_large' }, { status: 413 });
  let body: any;
  try { body = JSON.parse(raw); } catch { return NextResponse.json({ error: 'bad_json' }, { status: 400 }); }

  const jobId = String(body.job_id || body.jobId || '');
  if (!jobId) return NextResponse.json({ error: 'missing_job_id' }, { status: 400 });

  const r = await postResult(miner.id, jobId, {
    ok: body.ok !== false && !!body.b64,
    b64: typeof body.b64 === 'string' ? body.b64 : undefined,
    mime: typeof body.mime === 'string' ? body.mime : undefined,
    sha3: typeof body.sha3 === 'string' ? body.sha3 : undefined,
    meta: body.meta,
    error: typeof body.error === 'string' ? body.error : undefined,
  });

  if (!r.ok) {
    const status = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : 409;
    return NextResponse.json({ error: r.code }, { status });
  }
  return NextResponse.json({ ok: true, terminal: r.terminal, sha3: (r as any).sha3 });
}
