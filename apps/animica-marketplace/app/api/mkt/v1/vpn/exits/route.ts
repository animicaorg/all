import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { reapStaleExits } from '@/lib/vpn';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// Fields safe to expose on the public listing — NEVER proxyToken / owner internals.
const PUBLIC_EXIT_SELECT = {
  id: true, label: true, region: true, country: true, city: true,
  wgPubkey: true, endpoint: true, httpProxy: true, load: true,
  priceHintNanmPerGb: true, reputation: true, online: true,
} as const;

// GET /api/mkt/v1/vpn/exits[?region=&country=]  (scope: vpn)
// Ranked list of online exits for a client to pick from. Never returns proxyToken.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    reapStaleExits().catch(() => {});

    const url = new URL(req.url);
    const region = url.searchParams.get('region');
    const country = url.searchParams.get('country');
    const where: any = { online: true };
    if (region) where.region = region;
    if (country) where.country = country;

    const exits = await prisma.vpnExit.findMany({
      where,
      orderBy: [{ reputation: 'desc' }, { load: 'asc' }, { openSessions: 'asc' }, { lastSeenAt: 'desc' }],
      take: 200,
      select: PUBLIC_EXIT_SELECT,
    });
    return ok({ exits: jsonSafe(exits), count: exits.length });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/vpn/exits  (scope: vpn)
// Register/update the caller's exit from a descriptor. Upsert is keyed by wgPubkey; a wgPubkey owned
// by another account is rejected. Coming online refreshes lastSeenAt + online=true.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const body = await req.json().catch(() => ({}));

    return await withIdempotency(req, ctx, body, async () => {
      const wgPubkey = String(body.wgPubkey ?? '').trim();
      const label = String(body.label ?? '').trim().slice(0, 120);
      const region = String(body.region ?? '').trim().slice(0, 64);
      const country = String(body.country ?? '').trim().slice(0, 64);
      const city = String(body.city ?? '').trim().slice(0, 120);
      const endpoint = String(body.endpoint ?? '').trim().slice(0, 256);
      const tosHash = String(body.tosHash ?? '').trim().slice(0, 128);
      if (!wgPubkey) throw new ApiError(400, 'bad_exit', 'wgPubkey required');
      if (!label) throw new ApiError(400, 'bad_exit', 'label required');
      if (!region) throw new ApiError(400, 'bad_exit', 'region required');
      if (!country) throw new ApiError(400, 'bad_exit', 'country required');
      if (!endpoint) throw new ApiError(400, 'bad_exit', 'endpoint (host:port) required');
      if (!tosHash) throw new ApiError(400, 'bad_exit', 'tosHash required');

      const httpProxy = body.httpProxy ? String(body.httpProxy).trim().slice(0, 256) : null;
      const proxyToken = body.proxyToken ? String(body.proxyToken).slice(0, 256) : null;
      const capacityMbps = Math.max(0, Math.floor(Number(body.capacityMbps ?? 0)));
      if (!Number.isFinite(capacityMbps) || capacityMbps <= 0) {
        throw new ApiError(400, 'bad_exit', 'capacityMbps must be a positive integer');
      }
      let priceHintNanmPerGb: bigint;
      try {
        priceHintNanmPerGb = BigInt(String(body.priceHintNanmPerGb ?? '0').split('.')[0] || '0');
        if (priceHintNanmPerGb < 0n) throw new Error('negative');
      } catch {
        throw new ApiError(400, 'bad_exit', 'priceHintNanmPerGb must be a non-negative integer (nANM)');
      }

      const existing = await prisma.vpnExit.findUnique({ where: { wgPubkey } });
      if (existing && existing.ownerId !== ctx.accountId) {
        throw new ApiError(403, 'wg_taken', 'this wgPubkey is registered to another account');
      }

      const data = {
        label, region, country, city, endpoint,
        httpProxy, proxyToken, capacityMbps, priceHintNanmPerGb, tosHash,
        online: true, lastSeenAt: new Date(),
      };
      const exit = existing
        ? await prisma.vpnExit.update({ where: { id: existing.id }, data })
        : await prisma.vpnExit.create({ data: { ...data, wgPubkey, ownerId: ctx.accountId } });

      // Sanitized echo — omit proxyToken.
      const { proxyToken: _pt, ...safe } = exit;
      return {
        status: existing ? 200 : 201,
        data: {
          exit: jsonSafe({ ...safe, hasHttpProxy: !!exit.httpProxy }),
          registered: !existing,
          note: 'exit relay rewards accrue (pending settlement) as IOUs — not a spendable balance',
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
