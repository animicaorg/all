import { NextRequest, NextResponse } from 'next/server';
import { resolveMiner, postResultFile } from '@/lib/mediaQueue';
import { streamToFile, artifactPath, removeFile, StreamTooLargeError } from '@/lib/mediaStore';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// 9.0.0: a miner streams a BIG rendered artifact (upscaled video, stem zip, frame zip…)
// as a raw binary body — no base64, no JSON, no 48MB ceiling. The gateway re-hashes while
// streaming; a sha3 mismatch drops the bytes and leaves the job RUNNING for a clean retry.
// Query: job_id. Headers: x-anm-mime, x-anm-sha3, x-anm-meta (b64 JSON ≤2KB).

const MAX_ARTIFACT = 1024 * 1024 * 1024; // 1 GiB hard ceiling

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

export async function POST(req: NextRequest) {
  const miner = await resolveMiner(bearer(req));
  if (!miner) return NextResponse.json({ error: 'unregistered_miner' }, { status: 401 });

  const jobId = req.nextUrl.searchParams.get('job_id') || '';
  if (!jobId) return NextResponse.json({ error: 'missing_job_id' }, { status: 400 });

  // Cheap pre-checks before accepting a gigabyte of body.
  const job = await prisma.mediaJob.findUnique({ where: { id: jobId }, select: { claimedById: true, status: true } });
  if (!job) return NextResponse.json({ error: 'not_found' }, { status: 404 });
  if (job.claimedById !== miner.id) return NextResponse.json({ error: 'not_owner' }, { status: 403 });
  if (job.status !== 'RUNNING') return NextResponse.json({ error: 'not_running' }, { status: 409 });

  const mime = (req.headers.get('x-anm-mime') || 'application/octet-stream').split(';')[0].trim().slice(0, 100);
  const claimedSha3 = (req.headers.get('x-anm-sha3') || '').trim().toLowerCase();
  let meta: any = undefined;
  try {
    const rawMeta = req.headers.get('x-anm-meta');
    if (rawMeta && rawMeta.length <= 4096) meta = JSON.parse(Buffer.from(rawMeta, 'base64').toString('utf8'));
  } catch { /* meta is optional */ }

  const dest = artifactPath(jobId);
  let stored: { bytes: number; sha3: string };
  try {
    stored = await streamToFile(req.body, dest, MAX_ARTIFACT);
  } catch (e) {
    if (e instanceof StreamTooLargeError) return NextResponse.json({ error: 'result_too_large' }, { status: 413 });
    return NextResponse.json({ error: 'upload_failed' }, { status: 400 });
  }

  if (claimedSha3 && claimedSha3 !== stored.sha3) {
    await removeFile(dest);
    return NextResponse.json({ error: 'sha3_mismatch' }, { status: 400 });
  }

  const r = await postResultFile(miner.id, jobId, { path: dest, bytes: stored.bytes, sha3: stored.sha3, mime, meta });
  if (!r.ok) {
    await removeFile(dest);
    const status = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : 409;
    return NextResponse.json({ error: r.code }, { status });
  }
  return NextResponse.json({ ok: true, terminal: r.terminal, sha3: r.sha3, bytes: stored.bytes });
}
