export * from "./types";
export * from "./http";
export * from "./mock";

import { HttpAnimicaRpc } from "./http";
import { MockAnimicaRpc } from "./mock";
import type { AnimicaRpcClient } from "./types";

export function createAnimicaRpcFromEnv(env: Record<string, string | undefined>): AnimicaRpcClient {
  const rpcUrl = env.ANIMICA_RPC_URL ?? env.NEXT_PUBLIC_ANIMICA_RPC_URL;
  const chainId = env.ANIMICA_CHAIN_ID ?? env.NEXT_PUBLIC_ANIMICA_CHAIN_ID;
  if (!rpcUrl || env.LAUNCHPAD_USE_MOCK_RPC === "true") {
    return new MockAnimicaRpc();
  }
  return new HttpAnimicaRpc({
    rpcUrl,
    chainId: chainId ? Number(chainId) : undefined
  });
}
