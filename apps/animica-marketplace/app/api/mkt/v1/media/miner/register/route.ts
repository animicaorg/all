import { NextRequest, NextResponse } from 'next/server';
import { registerMiner, newMinerToken, CLAIMABLE_KINDS } from '@/lib/mediaQueue';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// A GPU media miner registers itself (idempotent, keyed by sha3 of its bearer token).
// The miner picks its own token; the gateway stores only the hash. Registration also
// acts as a heartbeat (updates online + lastSeenAt). No secret is required to register —
// the token IS the credential for claim/result, and rewards are IOU-only, so an attacker
// registering only adds render capacity. Body: { token?, label?, capabilities[], device?, maxPixels?, address? }.
export async function POST(req: NextRequest) {
  let body: any;
  try { body = await req.json(); } catch { return NextResponse.json({ error: 'bad_json' }, { status: 400 }); }

  const token = typeof body.token === 'string' && body.token.length >= 16 ? body.token : newMinerToken();
  const capabilities = Array.isArray(body.capabilities)
    ? body.capabilities.filter((c: any) => CLAIMABLE_KINDS.includes(c))
    : ['image'];

  const miner = await registerMiner({
    token,
    label: body.label,
    capabilities,
    device: body.device,
    maxPixels: body.maxPixels,
    address: body.address,
  });

  // Return the token ONLY when the gateway minted it (so the miner can persist it). If the
  // miner supplied its own token we don't echo it back.
  const minted = !(typeof body.token === 'string' && body.token.length >= 16);
  return NextResponse.json({
    ok: true,
    miner_id: miner.id,
    capabilities: miner.capabilities,
    jobs_done: miner.jobsDone,
    reward_nanm: miner.rewardNanm.toString(),
    settled_nanm: (miner as any).settledNanm?.toString?.() ?? '0',
    ...(minted ? { token } : {}),
    poll: {
      claim: '/api/mkt/v1/media/miner/claim',
      result: '/api/mkt/v1/media/miner/result',
      result_file: '/api/mkt/v1/media/miner/result-file',
      progress: '/api/mkt/v1/media/miner/progress',
    },
  });
}
