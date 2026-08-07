import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import {
  registerProvider,
  newProviderToken,
  DispatchError,
  PROVIDER_CAPABILITIES,
} from '@/lib/cloud/dispatch';
import { flags } from '@/lib/cloud/config';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// A compute provider self-registers (idempotent, keyed by sha3 of its bearer token — the
// media-miner pattern). The provider supplies a token (>=32 chars) or the server mints one;
// only the sha3-256 is ever stored. Unlike media miners, provider earnings are REAL spendable
// ledger balance, so registration requires a bech32m anim1... payout address and creates/links
// the ledger Account it pays into.
//
// Body: { address, token?, name?, capabilities?, cpu_cores?, memory_mb?, gpu? }
export async function POST(req: NextRequest) {
  try {
    if (!flags.computeMarket) throw new ApiError(503, 'disabled', 'the compute market is not enabled on this node');
    let body: any;
    try {
      body = await req.json();
    } catch {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    }

    const supplied = typeof body.token === 'string' && body.token.length >= 32;
    const token = supplied ? body.token : newProviderToken();

    const provider = await registerProvider({
      token,
      address: String(body.address ?? ''),
      name: typeof body.name === 'string' ? body.name : undefined,
      capabilities: Array.isArray(body.capabilities) ? body.capabilities.map(String) : undefined,
      cpuCores: body.cpu_cores ?? body.cpuCores,
      memoryMb: body.memory_mb ?? body.memoryMb,
      gpu: typeof body.gpu === 'string' ? body.gpu : null,
    });

    if (provider.status === 'SUSPENDED' || provider.status === 'DISABLED') {
      throw new ApiError(403, 'provider_suspended', provider.suspendedReason || 'this provider has been suspended');
    }

    return ok({
      ok: true,
      provider_id: provider.id,
      name: provider.name,
      status: provider.status,
      capabilities: provider.capabilities,
      allowed_capabilities: PROVIDER_CAPABILITIES,
      jobs_done: provider.jobsDone,
      earned_nanm: provider.earnedNanm,
      reputation: provider.reputation,
      // The token is echoed ONLY when the server minted it, so the worker can persist it.
      ...(supplied ? {} : { token }),
      poll: {
        claim: '/api/cloud/v1/providers/claim',
        heartbeat: '/api/cloud/v1/providers/heartbeat',
        result: '/api/cloud/v1/providers/result',
        fail: '/api/cloud/v1/providers/fail',
        runtime: '/api/cloud/v1/providers/runtime',
      },
    });
  } catch (e) {
    if (e instanceof DispatchError) return err(new ApiError(400, e.code, e.message));
    return err(e);
  }
}
