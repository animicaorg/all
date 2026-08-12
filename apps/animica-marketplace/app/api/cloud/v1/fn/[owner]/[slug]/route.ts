import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, ApiError, err } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';
import { invokeFunction } from '@/lib/cloud/executor';
import {
  anonIdentity,
  enforceBurst,
  consumeFreeExecution,
  refundFreeExecution,
  freeTierBudgetExhausted,
} from '@/lib/cloud/ratelimit';
import { limits, flags } from '@/lib/cloud/config';

// THE public endpoint for a deployed Animica Python Cloud function (§7).
//
//   POST /api/cloud/v1/fn/{owner}/{slug}     JSON body -> the function's `request`
//   GET  /api/cloud/v1/fn/{owner}/{slug}     query string -> the function's `request`
//
// {owner} is the developer's handle (or their anim1... address, so a function is reachable
// before a handle is claimed). This is the URL a developer shares, and the one that earns them
// ANM — so it is deliberately simple, CORS-open for public functions, and honest about cost:
// every response carries the execution's price and request id, and X-Animica-* headers let a
// client see what it was charged without parsing the body.

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

function corsHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'GET, POST, OPTIONS',
    'access-control-allow-headers': 'content-type, authorization, idempotency-key',
    'access-control-expose-headers': 'x-animica-request-id, x-animica-cost-nanm, x-animica-status',
    'access-control-max-age': '600',
    vary: 'origin',
  };
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders() });
}

export async function GET(req: NextRequest, ctx: { params: { owner: string; slug: string } }) {
  const payload: Record<string, string> = {};
  req.nextUrl.searchParams.forEach((v, k) => (payload[k] = v));
  return handle(req, ctx.params, payload);
}

export async function POST(req: NextRequest, ctx: { params: { owner: string; slug: string } }) {
  let payload: unknown = {};
  const raw = await req.text();
  if (raw) {
    if (raw.length > limits.maxRequestBytes) {
      return NextResponse.json(
        { error: { code: 'payload_too_large', message: `request body exceeds ${limits.maxRequestBytes} bytes` } },
        { status: 413, headers: corsHeaders() },
      );
    }
    try {
      payload = JSON.parse(raw);
    } catch {
      return NextResponse.json(
        { error: { code: 'bad_json', message: 'request body must be valid JSON' } },
        { status: 400, headers: corsHeaders() },
      );
    }
  }
  return handle(req, ctx.params, payload);
}

async function handle(req: NextRequest, params: { owner: string; slug: string }, payload: unknown) {
  const started = Date.now();
  let freeIdentity: string | null = null;

  try {
    if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Animica Python Cloud is not enabled on this node');

    const fn = await resolve(params.owner, params.slug);
    if (!fn) throw new ApiError(404, 'not_found', `no published function at ${params.owner}/${params.slug}`);

    // Auth is optional for public, free functions — that is what makes a shared link work for
    // anyone. Anything priced or private requires an identity that can actually be charged.
    let auth = null;
    try {
      auth = await authenticate(req);
    } catch (e) {
      // authenticate() throws 429 on key rate limit; surface it rather than falling through to
      // the anonymous path, which would let a rate-limited key keep spending the free tier.
      if (e instanceof ApiError && e.status === 429) throw e;
    }
    const callerAccountId = auth?.accountId ?? null;

    if (!callerAccountId) {
      if (fn.visibility !== 'PUBLIC') throw new ApiError(404, 'not_found', 'function not found');
      if (fn.requiresAuth || fn.perCallNanm > 0n) {
        throw new ApiError(401, 'auth_required', 'this function requires an API key. See /docs/cloud#auth');
      }
      // Anonymous free tier: burst limit, then a durable per-identity daily/monthly allowance,
      // then the platform-wide free-tier cost ceiling.
      freeIdentity = anonIdentity(req);
      enforceBurst(freeIdentity, { perMin: limits.rateAnonPerMin, scope: 'invoke' });
      if (await freeTierBudgetExhausted()) {
        throw new ApiError(
          429,
          'free_tier_exhausted',
          'The platform free-tier budget for this month is exhausted. Sign in and add ANM to continue.',
        );
      }
      const free = await consumeFreeExecution(freeIdentity);
      if (!free.ok) {
        const e = new ApiError(429, 'free_tier_limit', free.reason ?? 'free tier limit reached');
        e.details = { usedToday: free.usedToday, dailyLimit: free.dailyLimit, upgradeUrl: '/cloud/pricing' };
        throw e;
      }
    } else {
      enforceBurst(callerAccountId, { perMin: limits.rateUserPerMin, scope: 'invoke' });
    }

    const result = await invokeFunction({
      functionId: fn.id,
      payload,
      callerAccountId,
      callerKind: 'user',
    });

    const headers = {
      ...corsHeaders(),
      'x-animica-request-id': result.requestId,
      'x-animica-cost-nanm': result.receipt.grossNanm,
      'x-animica-status': result.status,
    };

    if (result.status === 'succeeded') {
      return NextResponse.json(jsonSafe(result.result ?? null), { status: 200, headers });
    }

    // A failed execution is a real, billed event — return the developer's error, not a generic
    // 500, so callers can debug. The receipt still tells them exactly what it cost.
    return NextResponse.json(
      jsonSafe({
        error: {
          code: result.status === 'timeout' ? 'function_timeout' : 'function_error',
          message: result.error ?? 'the function failed',
          type: result.errorType,
        },
        requestId: result.requestId,
        cost: { nanm: result.receipt.grossNanm, asset: 'ANM' },
      }),
      { status: result.status === 'timeout' ? 504 : 500, headers },
    );
  } catch (e) {
    // The caller never ran anything: give the free slot back rather than burning their allowance.
    if (freeIdentity) await refundFreeExecution(freeIdentity).catch(() => {});
    const res = err(e);
    for (const [k, v] of Object.entries(corsHeaders())) res.headers.set(k, v);
    res.headers.set('x-animica-duration-ms', String(Date.now() - started));
    return res;
  }
}

/** Resolve {owner}/{slug} where owner is a handle or a bech32m address. */
async function resolve(owner: string, slug: string) {
  const ownerKey = decodeURIComponent(owner).trim().toLowerCase();
  const account = await prisma.account.findFirst({
    where: { OR: [{ handle: ownerKey }, { address: decodeURIComponent(owner).trim() }] },
    select: { id: true },
  });
  if (!account) return null;
  return prisma.cloudFunction.findFirst({
    where: {
      ownerId: account.id,
      slug: decodeURIComponent(slug).trim().toLowerCase(),
      status: 'PUBLISHED',
      suspendedAt: null,
    },
    select: { id: true, visibility: true, requiresAuth: true, perCallNanm: true },
  });
}
