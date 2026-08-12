import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { limits } from '@/lib/cloud/config';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { validateSource, isValidatorFault } from '@/lib/cloud/validate';

export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/validate  { source, entrypoint? }
//
// Pre-deploy static validation: the exact same AST-only validator the deployment pipeline
// runs (sandbox/validate.py, never executes the code), so the editor can show findings with
// line numbers BEFORE a deploy is attempted. A broken validator is a 503 (platform fault,
// retry), never a silent pass — the pipeline fails closed the same way.

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    // Validation spawns a Python process — burst-bound it like any compute-ish endpoint.
    enforceBurst(ctx.accountId, { perMin: limits.rateUserPerMin, scope: 'validate' });

    const raw = await req.text();
    if (raw.length > limits.maxSourceBytes + 64 * 1024) {
      throw new ApiError(413, 'too_large', `request exceeds the source size limit (${limits.maxSourceBytes} bytes of Python)`);
    }
    let body: any;
    try {
      body = JSON.parse(raw || '{}');
    } catch {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    }
    if (typeof body.source !== 'string' || !body.source.trim()) {
      throw new ApiError(400, 'bad_request', 'source (Python text) is required');
    }
    const sizeBytes = Buffer.byteLength(body.source, 'utf8');
    if (sizeBytes > limits.maxSourceBytes) {
      throw new ApiError(413, 'too_large', `source is ${sizeBytes} bytes; the limit is ${limits.maxSourceBytes}`);
    }
    const entrypoint = String(body.entrypoint ?? 'main').trim() || 'main';

    const report = await validateSource({ source: body.source, entrypoint });
    if (isValidatorFault(report)) {
      throw new ApiError(503, 'validator_unavailable', report.findings[0]?.message ?? 'the validator is unavailable');
    }

    return ok({
      ok: report.ok,
      findings: report.findings, // [{severity, code, message, line, col}]
      functions: report.functions,
      imports: report.imports,
      capabilities: report.capabilities,
      sizeBytes,
      entrypoint,
    });
  } catch (e) {
    return err(e);
  }
}
