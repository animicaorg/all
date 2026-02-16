import { redis } from "@/src/server/db/redis";
import { env } from "@/src/server/env";

type RpcError = { code?: number; message?: string; data?: unknown };
type RpcAttempt = { method: string; params: unknown[]; error?: RpcError; result?: unknown };

type Discovery = {
  methods: { name: string; params?: Array<{ name?: string; schema?: { type?: string } }> }[];
};

const DISCOVERY_KEY = "animica:rpc:discover";

export async function rpcCall(method: string, params: unknown[] = []) {
  const response = await fetch(env.ANIMICA_RPC_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params })
  });
  const json = await response.json();
  if (json.error) throw json.error;
  return json.result;
}

export async function discover(): Promise<Discovery> {
  const cached = await redis.get(DISCOVERY_KEY);
  if (cached) return JSON.parse(cached);

  const result = await rpcCall("rpc.discover", []);
  const methods = result.methods ?? result.openrpc?.methods ?? [];
  const out = { methods };
  await redis.set(DISCOVERY_KEY, JSON.stringify(out), "EX", 600);
  return out;
}

const SEND_CANDIDATES = [
  "tx_sendRawTransaction",
  "tx.sendRawTransaction",
  "tx_submitRawTransaction",
  "tx.submitRawTransaction",
  "tx2.sendRawTransaction"
];

export function methodResolver(methods: string[]) {
  return SEND_CANDIDATES.find((candidate) => methods.includes(candidate)) ?? SEND_CANDIDATES[0];
}

export function paramEncoder(paramsSpec: Array<{ name?: string; schema?: { type?: string } }> | undefined, rawTx: string) {
  const first = paramsSpec?.[0];
  const isObject = first?.schema?.type === "object";
  const keyName = first?.name ?? "rawTx";

  if (isObject) {
    return {
      primary: [{ [keyName]: rawTx }],
      alternate: [rawTx]
    };
  }

  return {
    primary: [rawTx],
    alternate: [{ [keyName]: rawTx }]
  };
}

export async function ping() {
  const settled = await Promise.allSettled([
    rpcCall("node.ping", []),
    rpcCall("chain.getHead", []),
    rpcCall("state.getBalance", ["0x0"]),
    rpcCall("state.getNextNonce", ["0x0"])
  ]);
  return settled;
}

export async function defensiveSendRawTransaction(rawTx: string) {
  const attempts: RpcAttempt[] = [];
  const disc = await discover();
  const methodNames = disc.methods.map((m) => m.name);
  const sendMethod = methodResolver(methodNames);
  const methodMeta = disc.methods.find((m) => m.name === sendMethod);
  const encoding = paramEncoder(methodMeta?.params, rawTx);

  for (const preflight of ["tx.decodeRawTransaction", "tx.debugVerifyRawTransaction"]) {
    if (methodNames.includes(preflight)) {
      try {
        const result = await rpcCall(preflight, [rawTx]);
        attempts.push({ method: preflight, params: [rawTx], result });
      } catch (error: any) {
        attempts.push({ method: preflight, params: [rawTx], error });
      }
    }
  }

  try {
    const result = await rpcCall(sendMethod, encoding.primary as unknown[]);
    attempts.push({ method: sendMethod, params: encoding.primary as unknown[], result });
    return { ok: true, txHash: result, attempts };
  } catch (error: any) {
    attempts.push({ method: sendMethod, params: encoding.primary as unknown[], error });

    if (error?.code === -32602) {
      try {
        const result = await rpcCall(sendMethod, encoding.alternate as unknown[]);
        attempts.push({ method: sendMethod, params: encoding.alternate as unknown[], result });
        return { ok: true, txHash: result, attempts };
      } catch (retryError: any) {
        attempts.push({ method: sendMethod, params: encoding.alternate as unknown[], error: retryError });
        error = retryError;
      }
    }

    let explain: unknown = null;
    if (methodNames.includes("tx.explainReject")) {
      try {
        explain = await rpcCall("tx.explainReject", [rawTx]);
      } catch (explainError) {
        explain = explainError;
      }
    }

    return {
      ok: false,
      attempts,
      error: {
        code: error?.code,
        message: error?.message ?? "RPC send failed",
        data: error?.data,
        explain
      }
    };
  }
}
