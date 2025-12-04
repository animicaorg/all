import { namespacedStorage } from "../runtime";
import {
  KNOWN_NETWORKS,
  type Network,
  type NetworkId,
  findByChainId,
  getDefaultNetworkId,
  getNetwork,
} from "./networks";
import { RpcClient } from "./rpc";

const storage = namespacedStorage("net");

interface NetState {
  selectedId?: NetworkId;
}

async function readState(): Promise<NetState> {
  const stored = await storage.get<NetState>("state", {} as NetState);
  return stored ?? {};
}

async function writeState(next: NetState): Promise<void> {
  await storage.set("state", next as any);
}

export async function getSelectedNetwork(): Promise<Network> {
  const state = await readState();
  const id = state.selectedId ?? getDefaultNetworkId();
  try {
    return getNetwork(id);
  } catch {
    const fallback = getDefaultNetworkId();
    const net = getNetwork(fallback);
    await writeState({ selectedId: fallback });
    return net;
  }
}

export async function selectNetworkByChainId(chainId: number): Promise<Network> {
  const found = findByChainId(chainId);
  if (!found) {
    throw new Error(`Unknown network for chainId ${chainId}`);
  }
  await writeState({ selectedId: found.id });
  return found;
}

export async function listNetworks(): Promise<{ networks: Network[]; selected: Network }> {
  const selected = await getSelectedNetwork();
  return { networks: KNOWN_NETWORKS, selected };
}

export async function getRpcClient(): Promise<{ client: RpcClient; network: Network }> {
  const network = await getSelectedNetwork();
  const client = new RpcClient({ url: network.rpcHttp });
  return { client, network };
}

export async function rpcHealth(): Promise<{ ok: boolean; network: Network; error?: string }> {
  try {
    const { client, network } = await getRpcClient();
    const ok = await client.health();
    return { ok, network };
  } catch (err: any) {
    return { ok: false, network: await getSelectedNetwork(), error: err?.message ?? String(err) };
  }
}
