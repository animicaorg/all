import { redis } from "@/src/server/db/redis";
import { env } from "@/src/server/env";

type RpcSchemaParam = { name?: string; schema?: { type?: string } };
export type RpcMethod = { name: string; params?: RpcSchemaParam[] };
export type RpcDiscovery = { methods: RpcMethod[] };
export type RpcError = { code?: number; message?: string; data?: unknown };
export type RpcAttempt = { method: string; params: unknown[]; error?: RpcError; result?: unknown };

const DISCOVERY_KEY = "animica:rpc:discover";
const SEND_CANDIDATES = [
  "tx.sendRawTransaction",
  "tx_sendRawTransaction",
  "tx2.sendRawTransaction",
  "tx_submitRawTransaction",
  "tx.submitRawTransaction"
];

let inMemoryDiscovery: RpcDiscovery | null = null;

function nowId() {
  return Number(`${Date.now()}${Math.floor(Math.random() * 999)}`);
}

async function postRpc(method: string, params: unknown[] | Record<string, unknown>) {
  const response = await fetch(env.ANIMICA_RPC_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: nowId(), method, params })
  });
  const json = await response.json();
  if (json.error) throw json.error;
  return json.result;
}

export async function rpcCall(method: string, params: unknown[] | Record<string, unknown> = []) {
  return postRpc(method, params);
}

function expandMethodVariants(methodName: string) {
  return Array.from(new Set([methodName, methodName.replaceAll("_", "."), methodName.replaceAll(".", "_")]));
}

export async function discover(force = false): Promise<RpcDiscovery> {
  if (!force && inMemoryDiscovery) return inMemoryDiscovery;
  if (!force) {
    const cached = await redis.get(DISCOVERY_KEY);
    if (cached) {
      inMemoryDiscovery = JSON.parse(cached);
      return inMemoryDiscovery;
    }
  }

  const result = await postRpc("rpc.discover", []);
  const methods = result?.methods ?? result?.openrpc?.methods ?? [];
  inMemoryDiscovery = { methods };
  await redis.set(DISCOVERY_KEY, JSON.stringify(inMemoryDiscovery), "EX", 600);
  return inMemoryDiscovery;
}

export function methodResolver(methods: string[]) {
  return SEND_CANDIDATES.find((candidate) => methods.includes(candidate)) ?? SEND_CANDIDATES[0];
}

export function paramEncoder(paramsSpec: RpcSchemaParam[] | undefined, rawTx: string) {
  const first = paramsSpec?.[0];
  const keyName = first?.name ?? "rawTx";
  const objectPayload = { [keyName]: rawTx };
  const positionalPayload = [rawTx];
  if (first?.schema?.type === "object") {
    return { primary: [objectPayload], alternate: positionalPayload };
  }
  return { primary: positionalPayload, alternate: [objectPayload] };
}

export async function rpcCompatCall(methodName: string, params: unknown[] | Record<string, unknown>) {
  const discovery = await discover();
  const supported = new Set(discovery.methods.map((m) => m.name));

  let lastError: unknown;
  for (const variant of expandMethodVariants(methodName)) {
    if (!supported.has(variant)) continue;
    try {
      const normalizedParams = Array.isArray(params) ? params : [params];
      return await postRpc(variant, normalizedParams);
    } catch (error) {
      lastError = error;
    }
  }

  if (lastError) throw lastError;
  return postRpc(methodName, Array.isArray(params) ? params : [params]);
}

export async function defensiveSendRawTransaction(rawTx: string) {
  const attempts: RpcAttempt[] = [];
  const disc = await discover();
  const methodNames = disc.methods.map((m) => m.name);

  for (const method of SEND_CANDIDATES) {
    const resolved = methodNames.includes(method) ? method : undefined;
    if (!resolved) continue;
    const methodMeta = disc.methods.find((m) => m.name === resolved);
    const encoding = paramEncoder(methodMeta?.params, rawTx);

    for (const params of [encoding.primary, encoding.alternate]) {
      try {
        const result = await postRpc(resolved, params as unknown[]);
        attempts.push({ method: resolved, params: params as unknown[], result });
        return { ok: true as const, txHash: String(result), attempts, sendMethod: resolved };
      } catch (error: any) {
        attempts.push({ method: resolved, params: params as unknown[], error });
      }
    }
  }

  let explain: unknown = null;
  if (methodNames.includes("debug.explainReject")) {
    try {
      explain = await postRpc("debug.explainReject", [rawTx]);
    } catch (err) {
      explain = err;
    }
  }

  const last = attempts.at(-1)?.error;
  return {
    ok: false as const,
    attempts,
    error: {
      code: last?.code,
      message: last?.message ?? "RPC send failed for all method variants",
      data: last?.data,
      explain
    }
  };
}

export async function getTransactionReceipt(txHash: string) {
  const candidates = ["tx_getTransactionReceipt", "tx.getTransactionReceipt"];
  let lastError: unknown;
  for (const method of candidates) {
    try {
      return await rpcCompatCall(method, [txHash]);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error("No receipt method supported");
}

export async function pollReceipt(txHash: string, timeoutMs = 30_000, intervalMs = 1_500) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const receipt = await getTransactionReceipt(txHash).catch(() => null);
    if (receipt) return receipt;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("Timed out waiting for transaction receipt");
}

export async function bootRpcDiscovery() {
  return discover();
}
