import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { rateLimit } from '@/lib/apikey';
import { hashTriggerToken, enqueueRun, runDto, TRIGGER_TOKEN_PREFIX } from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/workers/[id]/trigger — the external trigger, free for every account.
// Auth is the single-purpose `anm_trg_` bearer token, NOT authenticate(): the caller is an
// external system (CI, cron, Zapier…) that must be able to fire exactly one worker and
// nothing else. The token grants no read access — the response is the queued run, no
// worker config.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const bearer = req.headers.get('authorization');
    const raw = bearer?.startsWith('Bearer ') ? bearer.slice(7).trim() : '';
    if (!raw.startsWith(TRIGGER_TOKEN_PREFIX)) throw new ApiError(401, 'unauthorized', 'trigger token required');
    // Constant lookup by sha256 — the raw token is never stored or logged.
    const worker = await prisma.worker.findUnique({ where: { triggerTokenHash: hashTriggerToken(raw) } });
    // Token/URL mismatch reads as invalid token, not 404 — no existence oracle for ids.
    if (!worker || worker.id !== params.id) throw new ApiError(401, 'unauthorized', 'invalid trigger token');

    // In-process burst limit per worker (single-instance app, same limiter as API keys) —
    // executions are unmetered, so this 6/min limiter IS the abuse control for tokens that
    // leak into a retry loop; the per-account concurrency cap in the runner backs it up.
    const rl = rateLimit('wtrg:' + worker.id, 6);
    if (!rl.ok) throw new ApiError(429, 'rate_limited', `retry after ${rl.retryAfter}s`);

    if (worker.status !== 'ACTIVE') throw new ApiError(409, 'worker_not_active', `worker is ${worker.status.toLowerCase()}`);
    const run = await enqueueRun(worker, 'external');
    return ok({ run: runDto(run) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
