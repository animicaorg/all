import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { resolveProvider, completeFleetJob } from '@/lib/cloud/dispatch';
import { limits } from '@/lib/cloud/config';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The whole report (result + stdout + logs) may not exceed this; the result value itself is
// separately capped at limits.maxOutputBytes inside completeFleetJob.
const MAX_BODY_BYTES = 8 * 1024 * 1024;

function bearer(req: NextRequest): string {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

// A provider posts the outcome of a job it claimed. Conditional CLAIMED/RUNNING -> DONE flip
// inside a transaction; completes the CloudExecution; SETTLES with the provider share — a
// REAL, spendable ledger credit (SALE_CREDIT), never an IOU, funded by the customer's payment
// for this very execution.
//
// Body: { job_id, status: 'ok'|'error'|'timeout', result?, error?, error_type?, stdout?,
//         logs?: [{level,message}], wall_ms, reported_cpu_ms?, max_rss_kb? }
export async function POST(req: NextRequest) {
  try {
    const provider = await resolveProvider(bearer(req));
    if (!provider) throw new ApiError(401, 'unregistered_provider', 'register at /api/cloud/v1/providers/register');

    const raw = await req.text();
    if (raw.length > MAX_BODY_BYTES) throw new ApiError(413, 'result_too_large', `report exceeds ${MAX_BODY_BYTES} bytes`);
    let body: any;
    try {
      body = JSON.parse(raw);
    } catch {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    }

    const jobId = String(body.job_id || '');
    if (!jobId) throw new ApiError(400, 'missing_job_id', 'job_id is required');
    const status = String(body.status || '');
    if (!['ok', 'error', 'timeout'].includes(status)) {
      throw new ApiError(400, 'bad_status', "status must be 'ok', 'error' or 'timeout' (use /providers/fail for infrastructure failures)");
    }

    const r = await completeFleetJob(provider, jobId, {
      status: status as 'ok' | 'error' | 'timeout',
      result: body.result,
      error: typeof body.error === 'string' ? body.error : undefined,
      errorType: typeof body.error_type === 'string' ? body.error_type : undefined,
      stdout: typeof body.stdout === 'string' ? body.stdout.slice(0, limits.maxOutputBytes) : '',
      logs: Array.isArray(body.logs) ? body.logs.slice(0, limits.maxLogLines) : [],
      wallMs: Number(body.wall_ms) || 0,
      reportedCpuMs: Number(body.reported_cpu_ms) || 0,
      maxRssKb: Number(body.max_rss_kb) || 0,
    });

    if (!r.ok) {
      const statusCode = r.code === 'not_found' ? 404 : r.code === 'not_owner' ? 403 : r.code === 'settle_failed' ? 502 : 409;
      throw new ApiError(statusCode, r.code, `result rejected: ${r.code}`);
    }

    return ok({
      ok: true,
      terminal: r.terminal,
      execution_status: r.executionStatus,
      price_nanm: r.priceNanm,
      payout_nanm: r.providerNanm,
      settled: r.settled,
    });
  } catch (e) {
    return err(e);
  }
}
