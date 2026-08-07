import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { resolveProvider, failFleetJob } from '@/lib/cloud/dispatch';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// A provider reports it COULD NOT run a job (docker missing mid-run, host OOM, image gone...).
// This is an infrastructure failure, distinct from user code raising — that goes to /result
// with status 'error'. The job is requeued while attempts remain, else terminally FAILED
// (and the execution is closed unbilled — the customer pays nothing for undelivered work).
// Repeated failures bleed provider reputation; at the floor the provider is suspended.
// Body: { job_id, error? }
export async function POST(req: NextRequest) {
  try {
    const provider = await resolveProvider(bearer(req));
    if (!provider) throw new ApiError(401, 'unregistered_provider', 'register at /api/cloud/v1/providers/register');

    let body: any;
    try {
      body = await req.json();
    } catch {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    }
    const jobId = String(body.job_id || '');
    if (!jobId) throw new ApiError(400, 'missing_job_id', 'job_id is required');

    const r = await failFleetJob(provider.id, jobId, typeof body.error === 'string' ? body.error : 'provider reported failure');
    if (!r.ok) {
      const status = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : 409;
      throw new ApiError(status, r.code, `failure report rejected: ${r.code}`);
    }
    return ok({ ok: true, terminal: r.terminal });
  } catch (e) {
    return err(e);
  }
}
