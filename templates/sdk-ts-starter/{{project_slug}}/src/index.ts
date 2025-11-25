// sdk-ts-starter: minimal, type-safe entry that talks to an Animica node.
// - Uses @animica/sdk if available
// - Gracefully falls back to a tiny in-file JSON-RPC client if not
//
// Quick run:
//   RPC_URL=http://127.0.0.1:8545 node --env-file=.env dist/index.js
// or with ts-node (dev):
//   RPC_URL=http://127.0.0.1:8545 npx ts-node src/index.ts

// -----------------------------
// Types
// -----------------------------
type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: Json | Json[];
}

interface JsonRpcSuccess<T = unknown> {
  jsonrpc: "2.0";
  id: number | string | null;
  result: T;
}

interface JsonRpcErrorData {
  code: number;
  message: string;
  data?: Json;
}

interface JsonRpcFailure {
  jsonrpc: "2.0";
  id: number | string | null;
  error: JsonRpcErrorData;
}

type JsonRpcResponse<T = unknown> = JsonRpcSuccess<T> | JsonRpcFailure;

type HeadView = {
  height: number;
  hash: string;   // 0x…
  time?: number;  // seconds since epoch (if exposed)
  // …other fields may exist depending on node version
};

// -----------------------------
// Tiny fallback JSON-RPC client
// -----------------------------
class FallbackHttpClient {
  constructor(
    private readonly url: string,
    private readonly timeoutMs = 10_000
  ) {}

  async request<T = unknown>(method: string, params?: Json | Json[]): Promise<T> {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), this.timeoutMs);

    const body: JsonRpcRequest = {
      jsonrpc: "2.0",
      id: Date.now(),
      method,
      params
    };

    const res = await fetch(this.url, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(body),
      signal: controller.signal
    }).catch((e) => {
      clearTimeout(t);
      throw new Error(`RPC network error: ${String(e)}`);
    });

    clearTimeout(t);

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`RPC HTTP ${res.status}: ${text || res.statusText}`);
    }

    const payload = (await res.json()) as JsonRpcResponse<T>;
    if ("error" in payload) {
      const { code, message, data } = payload.error;
      const extra = data ? ` | data=${JSON.stringify(data)}` : "";
      throw new Error(`RPC error ${code}: ${message}${extra}`);
    }
    return (payload as JsonRpcSuccess<T>).result;
  }
}

// -----------------------------
// SDK loader (optional, nice if present)
// -----------------------------
async function makeClient(rpcUrl: string) {
  try {
    // Dynamically import so this template works even if @animica/sdk
    // hasn’t been installed yet.
    const sdk = await import("@animica/sdk").catch(() => null as any);

    // Try a few common shapes for the HTTP client
    const candidate =
      sdk?.rpc?.http?.createHttpClient ??
      sdk?.rpc?.createHttpClient ??
      sdk?.createHttpClient ??
      sdk?.HttpClient;

    if (typeof candidate === "function") {
      // Many SDKs follow createHttpClient({ url, timeoutMs })
      return candidate({ url: rpcUrl, timeoutMs: 10_000 });
    }
    if (typeof candidate === "object" && candidate) {
      // Or class new HttpClient({ url, timeoutMs })
      // @ts-ignore - best-effort
      return new candidate({ url: rpcUrl, timeoutMs: 10_000 });
    }

    // Fallback if API surface doesn’t match expectations
    return new FallbackHttpClient(rpcUrl);
  } catch {
    // No SDK installed
    return new FallbackHttpClient(rpcUrl);
  }
}

// -----------------------------
// Helpers
// -----------------------------
function env(name: string, def?: string): string {
  const v = process.env[name] ?? def;
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

function pretty(v: unknown): string {
  return JSON.stringify(v, null, 2);
}

// -----------------------------
// Main
// -----------------------------
async function main() {
  const RPC_URL = env("RPC_URL", "http://127.0.0.1:8545");
  const client = await makeClient(RPC_URL);

  // 1) Chain params (economic/consensus snapshot)
  const params = await client.request("chain.getParams");
  console.log("• chain.getParams →");
  console.log(pretty(params));

  // 2) Current head
  const head = (await client.request("chain.getHead")) as HeadView;
  console.log("\n• chain.getHead →");
  console.log(pretty(head));

  // 3) (Optional) Look up block by number (if available on your node)
  try {
    const latest = await client.request("chain.getBlockByNumber", [head.height, { includeTxs: false }]);
    console.log(`\n• chain.getBlockByNumber(${head.height}) →`);
    console.log(pretty(latest));
  } catch (e) {
    console.log("\n• chain.getBlockByNumber not available on this node (or different signature). Skipping.");
    console.log(`  Reason: ${(e as Error).message}`);
  }

  // 4) (Optional) Balance/nonce helpers — shown as examples of common calls.
  //    Replace "anim1..." with a real address on your devnet/testnet.
  const SAMPLE_ADDRESS = process.env.ADDRESS ?? "anim1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
  try {
    const balance = await client.request("state.getBalance", [SAMPLE_ADDRESS]);
    const nonce   = await client.request("state.getNonce", [SAMPLE_ADDRESS]);
    console.log(`\n• state for ${SAMPLE_ADDRESS}`);
    console.log(pretty({ balance, nonce }));
  } catch (e) {
    console.log("\n• state.getBalance/state.getNonce not available or bad address. Skipping.");
    console.log(`  Reason: ${(e as Error).message}`);
  }

  // 5) (Optional) Sending a raw transaction:
  //    If you use @animica/sdk, prefer its tx builder + signer.
  //    The raw method here is included only as a reference.
  //
  //    Example (pseudocode):
  //    import { wallet, tx } from "@animica/sdk";
  //    const signer = await wallet.fromMnemonic(process.env.MNEMONIC!);
  //    const built  = await tx.build.transfer({ to, amount, chainId });
  //    const signed = await signer.sign(built);
  //    const hash   = await client.request("tx.sendRawTransaction", [signed.cborHex]);
  //    console.log("tx hash:", hash);
}

main().catch((err) => {
  console.error("\n✖ Fatal:", err instanceof Error ? err.message : String(err));
  process.exitCode = 1;
});
