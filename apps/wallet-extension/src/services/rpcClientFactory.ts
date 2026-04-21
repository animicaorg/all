import { RpcClient } from '../core/rpc/client';
import { getEffectiveRpcUrl, getRpcUrl } from './rpcConfig';

let currentClient: RpcClient | null = null;
let currentRpcUrl: string | null = null;

export async function getRpcClient(defaultRpcUrl?: string): Promise<RpcClient> {
  const rpcUrl = await getRpcUrl(defaultRpcUrl);
  if (!currentClient || currentRpcUrl !== rpcUrl) {
    currentClient = new RpcClient([rpcUrl]);
    currentRpcUrl = rpcUrl;
  }

  return currentClient;
}

export function recreateRpcClient(rpcUrl?: string): RpcClient {
  const targetRpcUrl = rpcUrl || getEffectiveRpcUrl();
  currentClient = new RpcClient([targetRpcUrl]);
  currentRpcUrl = targetRpcUrl;
  return currentClient;
}

export function clearRpcClient(): void {
  currentClient = null;
  currentRpcUrl = null;
}
