import { NextRequest, NextResponse } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { resolveProvider, claimNextJob } from '@/lib/cloud/dispatch';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// A registered provider polls for the next job it can serve. Atomic SKIP LOCKED claim
// (two providers can never grab the same job); sets a lease the provider must extend via
// /heartbeat. 204 when the queue has nothing for this provider. Also acts as a heartbeat.
export async function POST(req: NextRequest) {
  try {
    const provider = await resolveProvider(bearer(req));
    if (!provider) throw new ApiError(401, 'unregistered_provider', 'register at /api/cloud/v1/providers/register');
    if (provider.status === 'SUSPENDED' || provider.status === 'DISABLED') {
      throw new ApiError(403, 'provider_suspended', provider.suspendedReason || 'this provider has been suspended');
    }

    const job = await claimNextJob(provider);
    if (!job) return new NextResponse(null, { status: 204 });

    return ok({
      job: {
        id: job.jobId,
        execution_id: job.executionId,
        request_id: job.requestId,
        source: job.source,
        entrypoint: job.entrypoint,
        request: job.request,
        meta: job.meta,
        timeout_ms: job.timeoutMs,
        memory_mb: job.memoryMb,
        attempts: job.attempts,
        lease_seconds: job.leaseSeconds,
        image: job.image,
      },
    });
  } catch (e) {
    return err(e);
  }
}
