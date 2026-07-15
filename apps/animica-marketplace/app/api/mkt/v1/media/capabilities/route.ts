import { NextRequest } from 'next/server';
import { publicOk, publicPreflight } from '@/lib/api';
import { MEDIA_KINDS, onlineCapabilityCounts } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export function OPTIONS() {
  return publicPreflight();
}

// Live media capability: how many miners are online RIGHT NOW for each kind.
// The homepage studios poll this so they can tell a user up-front whether a kind
// (image / video / music) has a renderer online, instead of silently queuing a
// job that no online miner can serve. Public read; no inputs, no secrets.
// One grouped query (not one-per-kind) since this is polled by every visitor.
export async function GET(_req: NextRequest) {
  const kinds = await onlineCapabilityCounts();
  const maxOnline = MEDIA_KINDS.reduce((m, k) => Math.max(m, kinds[k] || 0), 0);
  return publicOk({ kinds, anyOnline: maxOnline > 0, ts: Date.now() });
}
