import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { resolveProvider, heartbeatJob } from '@/lib/cloud/dispatch';
import { prisma } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// Heartbeat: refreshes lastSeenAt, and — when a job_id is given — extends that job's lease so
// a long-running execution is never swept out from under a live provider.
// Body: { job_id? }
export async function POST(req: NextRequest) {
  try {
    const provider = await resolveProvider(bearer(req));
    if (!provider) throw new ApiError(401, 'unregistered_provider', 'register at /api/cloud/v1/providers/register');

    let body: any = {};
    try {
      body = await req.json();
    } catch {
      /* empty body is a plain liveness ping */
    }

    const jobId = typeof body.job_id === 'string' ? body.job_id : '';
    if (!jobId) {
      await prisma.cloudProvider
        .update({ where: { id: provider.id }, data: { lastSeenAt: new Date(), ...(provider.status === 'IDLE' ? { status: 'ACTIVE' } : {}) } })
        .catch(() => {});
      return ok({ ok: true });
    }

    const r = await heartbeatJob(provider.id, jobId);
    if (!r.ok) {
      const status = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : 409;
      throw new ApiError(status, r.code!, `heartbeat rejected: ${r.code}`);
    }
    return ok({ ok: true, lease_until: r.leaseUntil?.toISOString() });
  } catch (e) {
    return err(e);
  }
}
