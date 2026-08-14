import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { flags, limits } from '@/lib/cloud/config';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { invokeFunction } from '@/lib/cloud/executor';
import { parseNanm } from '../../shared';

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/cloud/v1/functions/[id]/invoke  { payload?, maxSpendNanm? }
//
// Authenticated invoke by function id — the console "Run" button and SDK test path. The owner
// may run their own function (included in their plan's execution quota, no ANM charge); anyone
// else with access pays the metered price exactly like the public /fn endpoint. PRIVATE
// functions are invocable by their owner only, and are a 404 to everyone else.

export async function POST(req: NextRequest, route: { params: { id: string } }) {
  try {
    if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Animica Python Cloud is not enabled on this node');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'use');
    enforceBurst(ctx.accountId, { perMin: limits.rateUserPerMin, scope: 'invoke' });

    const fn = await prisma.cloudFunction.findUnique({
      where: { id: route.params.id },
      select: { id: true, ownerId: true, visibility: true },
    });
    if (!fn) throw new ApiError(404, 'not_found', 'function not found');
    const isOwner = fn.ownerId === ctx.accountId;
    if (!isOwner && fn.visibility === 'PRIVATE') throw new ApiError(404, 'not_found', 'function not found');

    const raw = await req.text();
    if (raw.length > limits.maxRequestBytes) {
      throw new ApiError(413, 'payload_too_large', `request body exceeds ${limits.maxRequestBytes} bytes`);
    }
    let body: any = {};
    if (raw) {
      try {
        body = JSON.parse(raw);
      } catch {
        throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
      }
    }
    const maxSpendNanm = body?.maxSpendNanm != null ? parseNanm(body.maxSpendNanm, 'maxSpendNanm') : null;

    return await withIdempotency(req, ctx, body, async () => {
      // invokeFunction is the single execution path: admission, quota, affordability,
      // sandbox run, metering, pricing and exactly-once ledger settlement.
      const result = await invokeFunction({
        functionId: fn.id,
        payload: body?.payload ?? {},
        callerAccountId: ctx.accountId,
        callerKind: 'user',
        maxSpendNanm,
      });
      return {
        status: 200,
        data: {
          requestId: result.requestId,
          status: result.status,
          result: result.result ?? null,
          error: result.error ?? null,
          errorType: result.errorType ?? null,
          traceback: result.traceback ?? null,
          logs: result.logs,
          stdout: result.stdout,
          durationMs: result.durationMs,
          receipt: result.receipt,
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
