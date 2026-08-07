import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { limits } from '@/lib/cloud/config';
import { activePolicy, estimate } from '@/lib/cloud/pricing';
import { feeBpsForDeveloper } from '@/lib/cloud/entitlements';
import { anchorReadiness } from '@/lib/cloud/anchor';

export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/estimate
//   { functionId? }                                     -> estimate for an existing function
//   { timeoutMs?, memoryMb?, expectedAiTokens?, surchargeNanm? }  -> estimate for a config
//
// Uses the live PricingPolicy through pricing.estimate(): a typical case (25% of the timeout
// budget) and the worst case (full timeout + max AI tokens), both as full per-line-item
// breakdowns in integer nANM. The feeBps applied is the CALLER'S real rate (Founding
// Developer discounts included), so the number shown is the number that will be charged.

function clampInt(v: unknown, min: number, max: number, fallback: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.trunc(n), min), max);
}

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });

    let timeoutMs = clampInt(body.timeoutMs, limits.minTimeoutMs, limits.maxTimeoutMs, limits.defaultTimeoutMs);
    let memoryMb = clampInt(body.memoryMb, limits.minMemoryMb, limits.maxMemoryMb, limits.defaultMemoryMb);
    let surchargeNanm = 0n;
    if (body.surchargeNanm != null) {
      try {
        surchargeNanm = BigInt(String(body.surchargeNanm).trim());
        if (surchargeNanm < 0n) throw new Error('negative');
      } catch {
        throw new ApiError(400, 'bad_request', 'surchargeNanm must be a non-negative integer amount in nANM');
      }
    }
    const expectedAiTokens = clampInt(body.expectedAiTokens, 0, limits.maxAiTokensPerExecution, 0);

    if (body.functionId != null) {
      const fn = await prisma.cloudFunction.findUnique({
        where: { id: String(body.functionId) },
        select: { ownerId: true, visibility: true, status: true, timeoutMs: true, memoryMb: true, perCallNanm: true },
      });
      // Owner always; anyone else only for functions they could actually call.
      const visible = fn && (fn.ownerId === ctx.accountId || (fn.status === 'PUBLISHED' && fn.visibility !== 'PRIVATE'));
      if (!fn || !visible) throw new ApiError(404, 'not_found', 'function not found');
      timeoutMs = fn.timeoutMs;
      memoryMb = fn.memoryMb;
      surchargeNanm = fn.perCallNanm;
    }

    const policy = await activePolicy();
    const feeBps = await feeBpsForDeveloper(ctx.accountId, policy.platformFeeBps);
    const est = estimate({ timeoutMs, memoryMb, expectedAiTokens, surchargeNanm, feeBps }, policy);

    // The deploy editor reads `perCall*Nanm` and `anchor.*`. Returning only
    // `typicalNanm`/`maxNanm` and no `anchor` made it read `anchor.willBroadcast`
    // on undefined and crash the page. Both spellings are sent so the older field
    // names keep working for anything else already reading them.
    const anchor = await anchorReadiness();
    return ok({
      input: { timeoutMs, memoryMb, expectedAiTokens, surchargeNanm },
      typicalNanm: est.typicalNanm,
      maxNanm: est.maxNanm,
      perCallTypicalNanm: est.typicalNanm,
      perCallMaxNanm: est.maxNanm,
      anchor,
      typical: est.typical,
      worst: est.worst,
      feeBps,
      policyVersion: policy.version,
      note: 'typical assumes 25% of the timeout budget is consumed; max is the worst case a caller should authorize.',
    });
  } catch (e) {
    return err(e);
  }
}
