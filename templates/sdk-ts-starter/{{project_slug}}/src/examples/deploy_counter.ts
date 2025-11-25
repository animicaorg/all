/**
 * deploy_counter.ts
 * ------------------
 * End-to-end example: deploy a Counter contract package (manifest + code) to an Animica devnet/testnet
 * using the TypeScript SDK if available. Falls back to a tiny JSON-RPC client for read calls.
 *
 * Usage (Node / ts-node):
 *   # 1) Ensure you've built a deployable package (manifest.json with code bytes & ABI)
 *   #    For this starter, point MANIFEST_PATH at your built artifact:
 *   #      MANIFEST_PATH=./artifacts/counter/manifest.json
 *   #
 *   # 2) Provide an account (PQ signer) via MNEMONIC (preferred). CHAIN_ID & RPC_URL must match your node.
 *   #
 *   # With ts-node:
 *   #   RPC_URL=http://127.0.0.1:8545 CHAIN_ID=1 MNEMONIC="abandon ..." \
 *   #   npx ts-node templates/sdk-ts-starter/{{project_slug}}/src/examples/deploy_counter.ts
 *   #
 *   # Or after building to JS (tsc):
 *   #   node dist/examples/deploy_counter.js
 *
 * Notes:
 * - This example *prefers* @animica/sdk for building/signing/sending the deploy transaction.
 * - If @animica/sdk is not installed, the script can still read node state via fallback RPC,
 *   but it cannot sign PQ transactions — you'll need the SDK for that step.
 * - The manifest format should match your toolchain (name, abi, code/codeHash fields).
 */

/* eslint-disable no-console */

import { readFile } from "fs/promises";
import * as path from "path";

// -----------------------------
// Types (lightweight)
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

interface JsonRpcError {
  jsonrpc: "2.0";
  id: number | string | null;
  error: {
    code: number;
    message: string;
    data?: Json;
  };
}

type JsonRpcResponse<T = unknown> = JsonRpcSuccess<T> | JsonRpcError;

type Hex = string;

interface DeployManifest {
  name: string;
  abi: any;
  // Implementations vary; include the fields your toolchain emits.
  // At least one of these should be present:
  code?: Hex;       // hex-encoded bytes
  codeHex?: Hex;    // some pipelines use a different key
  code_hash?: Hex;  // informational; node verifies against bytes it receives
  codeHash?: Hex;
  [k: string]: unknown;
}

interface ReceiptView {
  status: "SUCCESS" | "REVERT" | "OOG" | string;
  gasUsed?: number;
  logs?: Array<{ address: string; topics: Hex[]; data: Hex }>;
  contractAddress?: string; // many nodes return created address here
}

// -----------------------------
// Minimal fallback JSON-RPC client (read calls / tx send passthrough)
// -----------------------------
class FallbackHttpClient {
  constructor(
    private readonly url: string,
    private readonly timeoutMs = 10_000
  ) {}

  async request<T = unknown>(method: string, params?: Json | Json[]): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    const body: JsonRpcRequest = { jsonrpc: "2.0", id: Date.now(), method, params };
    const res = await fetch(this.url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).catch((e) => {
      clearTimeout(timer);
      throw new Error(`RPC network error: ${String(e)}`);
    });

    clearTimeout(timer);

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
// Env helpers
// -----------------------------
function env(name: string, def?: string): string {
  const v = process.env[name] ?? def;
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

function optionalEnv(name: string, def?: string): string | undefined {
  return process.env[name] ?? def;
}

function pretty(v: unknown): string {
  return JSON.stringify(v, null, 2);
}

// -----------------------------
// Retry/poll utility
// -----------------------------
async function waitFor<T>(
  fn: () => Promise<T>,
  isDone: (v: T) => boolean,
  opts: { intervalMs?: number; maxMs?: number } = {}
): Promise<T> {
  const start = Date.now();
  const interval = opts.intervalMs ?? 1_000;
  const max = opts.maxMs ?? 60_000;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const v = await fn();
    if (isDone(v)) return v;
    if (Date.now() - start > max) throw new Error("Timeout waiting for condition.");
    await new Promise((r) => setTimeout(r, interval));
  }
}

// -----------------------------
// SDK loader (dynamic, optional)
// -----------------------------
async function loadSdk() {
  try {
    // dynamic import so the example runs even if @animica/sdk is not installed
    // @ts-ignore
    return await import("@animica/sdk");
  } catch {
    return null;
  }
}

function resolveCodeBytes(manifest: DeployManifest): Hex {
  if (typeof manifest.code === "string") return manifest.code as Hex;
  if (typeof manifest.codeHex === "string") return manifest.codeHex as Hex;
  // Some build pipelines store bytes under manifest.artifact.code or similar
  const maybe = (manifest as any)?.artifact?.code;
  if (typeof maybe === "string") return maybe as Hex;
  throw new Error("Manifest missing code/codeHex field with hex-encoded bytes.");
}

// -----------------------------
// Main deployment flow
// -----------------------------
async function main() {
  const RPC_URL = env("RPC_URL", "http://127.0.0.1:8545");
  const CHAIN_ID = Number(env("CHAIN_ID", "1"));
  const MANIFEST_PATH = env("MANIFEST_PATH"); // e.g., ./artifacts/counter/manifest.json
  const MNEMONIC = optionalEnv("MNEMONIC");

  const sdk = await loadSdk();

  // HTTP client (prefer SDK's helper if present)
  const http =
    sdk?.rpc?.http?.createHttpClient
      ? sdk.rpc.http.createHttpClient({ url: RPC_URL, timeoutMs: 10_000 })
      : new FallbackHttpClient(RPC_URL);

  // Load & parse manifest
  const resolved = path.resolve(MANIFEST_PATH);
  const manifestJson = JSON.parse(await readFile(resolved, "utf-8")) as DeployManifest;
  const codeHex = resolveCodeBytes(manifestJson);

  console.log("• Loaded manifest:");
  console.log(pretty({ name: manifestJson.name, hasCodeBytes: codeHex?.startsWith("0x"), abiFunctions: manifestJson.abi?.functions?.length ?? "?" }));

  // If SDK is not available, we cannot sign a PQ tx here. Provide guidance and exit early.
  if (!sdk) {
    console.log("\nℹ @animica/sdk not found. Install it to enable signing & deploy:");
    console.log("   npm i -D @animica/sdk   # or: pnpm add -D @animica/sdk");
    console.log("Once installed, re-run with MNEMONIC, RPC_URL, CHAIN_ID set.");
    return;
  }

  // Resolve signer from mnemonic (Dilithium3/SPHINCS+, SDK picks default or allow override)
  if (!MNEMONIC) {
    throw new Error("MNEMONIC is required to sign the deploy transaction.");
  }

  const walletMod = (sdk as any).wallet ?? {};
  const fromMnemonic =
    walletMod?.fromMnemonic ??
    walletMod?.mnemonic?.fromMnemonic ??
    walletMod?.createFromMnemonic;

  if (typeof fromMnemonic !== "function") {
    throw new Error("SDK wallet mnemonic helper not found. Check your @animica/sdk version.");
  }

  const signer = await fromMnemonic(MNEMONIC);
  const fromAddr =
    typeof signer?.getAddress === "function"
      ? await signer.getAddress()
      : signer?.address ?? "unknown";

  console.log(`\n• Using account: ${fromAddr}`);

  // Build a deploy transaction using the SDK (preferred path).
  // We accommodate a few possible SDK shapes for forward-compatibility.
  const txMod = (sdk as any).tx ?? {};
  const contractsMod = (sdk as any).contracts ?? {};
  let txHash: string | undefined;
  let createdAddress: string | undefined;
  let receipt: ReceiptView | undefined;

  try {
    if (contractsMod?.deployer?.deploy) {
      // Style A: contracts.deployer.deploy(client, { manifest, chainId }, signer)
      const result = await contractsMod.deployer.deploy(http, { manifest: manifestJson, chainId: CHAIN_ID }, signer);
      txHash = result?.txHash ?? result?.hash ?? result;
      createdAddress = result?.contractAddress ?? result?.address;
    } else if (typeof contractsMod?.Deployer === "function") {
      // Style B: new Deployer(client).deploy({ manifest, chainId }, signer)
      const dep = new contractsMod.Deployer(http);
      const result = await dep.deploy({ manifest: manifestJson, chainId: CHAIN_ID }, signer);
      txHash = result?.txHash ?? result?.hash ?? result;
      createdAddress = result?.contractAddress ?? result?.address;
    } else if (txMod?.build?.deploy && txMod?.send) {
      // Style C: tx.build.deploy(...) → signer.sign(...) → tx.send(...)
      const built = await txMod.build.deploy({
        manifest: manifestJson,
        chainId: CHAIN_ID,
        from: fromAddr,
      });
      const signed = await signer.sign(built);
      txHash = await txMod.send(http, signed);
    } else {
      throw new Error("SDK does not expose a recognized deploy API (contracts.deployer or tx.build.deploy).");
    }
  } catch (e) {
    console.error("\n✖ Failed to build/send deploy tx via SDK:", (e as Error).message);
    throw e;
  }

  if (!txHash) throw new Error("Deploy flow did not return a tx hash.");

  console.log("\n• Sent deploy transaction:");
  console.log(pretty({ txHash, from: fromAddr }));

  // Poll for receipt
  receipt = await waitFor(
    async () => {
      try {
        return (await (http as any).request("tx.getTransactionReceipt", [txHash])) as ReceiptView;
      } catch {
        return undefined as unknown as ReceiptView;
      }
    },
    (r) => !!r && typeof r === "object" && !!(r as any).status,
    { intervalMs: 1_000, maxMs: 120_000 }
  );

  console.log("\n• Receipt:");
  console.log(pretty(receipt));

  // Determine created address, if not already known
  if (!createdAddress) {
    createdAddress =
      (receipt as any)?.contractAddress ??
      (receipt as any)?.address ??
      undefined;
  }

  if (!createdAddress) {
    console.log("\n⚠ Could not determine contract address from receipt. Your node may report it via logs or a different field.");
  } else {
    console.log(`\n• Contract deployed at: ${createdAddress}`);
  }

  // Optional: simple call via RPC to verify the contract responds (depends on your ABI)
  // If your Counter ABI has a "get()" read, uncomment:
  //
  // try {
  //   const res = await (http as any).request("state.call", [{
  //     to: createdAddress,
  //     data: /* ABI-encoded "get" */ "0x",
  //   }]);
  //   console.log("\n• Counter.get() →", res);
  // } catch (e) {
  //   console.log("\n• Skipping verify call (state.call not enabled or ABI encoding not provided).");
  // }

  console.log("\n✓ Done.");
}

main().catch((err) => {
  console.error("\n✖ Fatal:", err instanceof Error ? err.message : String(err));
  process.exitCode = 1;
});
