import { NextRequest } from 'next/server';
import { publicOk, publicPreflight, err } from '@/lib/api';
import { prisma } from '@/lib/db';
import { reapStaleExits } from '@/lib/vpn';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/vpn/locations[?region=&country=]  (PUBLIC, no auth)
//
// The wallet-less clients (browser extension VPN picker, /vpn landing page) need to LIST exit
// locations without signing in — picking a location reveals nothing sensitive. Auth is only
// required to actually open a session (POST /vpn/exits/[id]/peer, scope: vpn). This route never
// returns the proxyToken; it only exposes a boolean `proxyAuth` so a picker knows whether to
// prompt for an access token.
export async function GET(req: NextRequest) {
  try {
    reapStaleExits().catch(() => {});
    const url = new URL(req.url);
    const region = url.searchParams.get('region');
    const country = url.searchParams.get('country');
    const where: any = { online: true };
    if (region) where.region = region;
    if (country) where.country = country;

    const rows = await prisma.vpnExit.findMany({
      where,
      orderBy: [{ reputation: 'desc' }, { load: 'asc' }, { openSessions: 'asc' }, { lastSeenAt: 'desc' }],
      take: 200,
      select: {
        id: true, label: true, region: true, country: true, city: true,
        httpProxy: true, load: true, reputation: true, online: true,
        proxyToken: true, // server-only — converted to a boolean below, never emitted
      },
    });

    const exits = rows.map(({ proxyToken, ...e }) => ({ ...e, proxyAuth: !!proxyToken }));
    return publicOk({ exits: jsonSafe(exits), count: exits.length });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight — the extension popup and native .anm sites read this cross-origin.
export async function OPTIONS() {
  return publicPreflight();
}
