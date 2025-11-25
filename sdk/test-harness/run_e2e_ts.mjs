#!/usr/bin/env node
/**
 * Animica SDK — TypeScript E2E (run via Node ESM): deploy + call Counter
 *
 * Usage:
 *   node sdk/test-harness/run_e2e_ts.mjs \
 *     --rpc http://127.0.0.1:8545 \
 *     --chain 1337 \
 *     --alg dilithium3 \
 *     --mnemonic "abandon abandon ..."
 *
 * Env overrides:
 *   RPC_URL, WS_URL, CHAIN_ID, ALG_ID, MNEMONIC, ACCOUNT_INDEX
 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** Resolve paths relative to this file */
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const HARNESS_ROOT = path.resolve(__dirname);
const CONTRACT_DIR = path.join(HARNESS_ROOT, "contracts", "counter");
const FIXTURES_DIR = path.join(HARNESS_ROOT, "fixtures");

const DEFAULT_RPC = "http://127.0.0.1:8545";
const DEFAULT_WS = "ws://127.0.0.1:8545/ws";

/** Best-effort loader: prefer installed @animica/sdk; fallback to local dist if present. */
async function loadSdk() {
  try {
    return await import("@animica/sdk");
  } catch {
    // Try local dist build within monorepo
    const localDist = pathToFileURL(
      path.resolve(__dirname, "../typescript/dist/index.js")
    ).href;
    try {
      return await import(localDist);
    } catch (e) {
      console.error(
        "[e2e-ts] Failed to import @animica/sdk and local dist. Did you build the TS SDK?",
        e
      );
      process.exit(2);
    }
  }
}

/** Minimal CLI parsing */
function parseArgs(argv) {
  const out = {
    rpc: process.env.RPC_URL || DEFAULT_RPC,
    ws: process.env.WS_URL || DEFAULT_WS,
    chain: process.env.CHAIN_ID ? Number(process.env.CHAIN_ID) : 0,
    alg: process.env.ALG_ID || "dilithium3",
    mnemonic: process.env.MNEMONIC || undefined,
    accountIndex: process.env.ACCOUNT_INDEX ? Number(process.env.ACCOUNT_INDEX) : 0
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const nxt = () => argv[i + 1];
    if (a === "--rpc") out.rpc = nxt();
    else if (a === "--ws") out.ws = nxt();
    else if (a === "--chain") out.chain = Number(nxt());
    else if (a === "--alg") out.alg = nxt();
    else if (a === "--mnemonic") out.mnemonic = nxt();
    else if (a === "--account-index") out.accountIndex = Number(nxt());
  }
  return out;
}

async function detectChainId(rpc) {
  const cid = await rpc.call("chain.getChainId", []);
  if (typeof cid !== "number") throw new Error("chain.getChainId did not return number");
  return cid;
}

async function loadManifestAndCode() {
  const manifestPath = path.join(CONTRACT_DIR, "manifest.json");
  const codePath = path.join(CONTRACT_DIR, "contract.py");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const code = await readFile(codePath);
  return { manifest, code };
}

async function loadFundedFixture() {
  try {
    const txt = await readFile(path.join(FIXTURES_DIR, "accounts.json"), "utf8");
    return JSON.parse(txt);
  } catch {
    return null;
  }
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

async function makeSigner(SDK, cfg, chainId) {
  const { wallet } = SDK;
  if (cfg.mnemonic) {
    const seed = await wallet.mnemonic.mnemonicToSeed(cfg.mnemonic);
    return await wallet.signer.fromSeed({
      seed,
      algId: cfg.alg,
      accountIndex: cfg.accountIndex,
      chainId
    });
  }
  const fx = await loadFundedFixture();
  if (fx?.mnemonic) {
    const seed = await wallet.mnemonic.mnemonicToSeed(fx.mnemonic);
    const alg = fx.alg || cfg.alg;
    const idx = fx.index != null ? Number(fx.index) : cfg.accountIndex;
    return await wallet.signer.fromSeed({ seed, algId: alg, accountIndex: idx, chainId });
  }
  // Ephemeral (devnet should prefund standard derivations)
  const { mnemonic } = await wallet.mnemonic.createMnemonic();
  const seed = await wallet.mnemonic.mnemonicToSeed(mnemonic);
  console.warn("[warn] Using ephemeral mnemonic; ensure devnet prefunds defaults.");
  return await wallet.signer.fromSeed({
    seed,
    algId: cfg.alg,
    accountIndex: cfg.accountIndex,
    chainId
  });
}

async function deployCounter(SDK, rpc, chainId, signer) {
  const { tx, address } = SDK;
  const { manifest, code } = await loadManifestAndCode();

  const built = await tx.build.buildDeploy({
    chainId,
    senderPubkey: await signer.publicKeyBytes(),
    algId: signer.algId,
    manifest,
    code
  });

  const signBytes = await tx.encode.signBytesForTx(built);
  const signature = await signer.sign(signBytes, "tx");
  const raw = await tx.build.attachSignature(built, signature);

  const txHash = await tx.send.sendRawTransaction(rpc, raw);
  const receipt = await tx.send.waitForReceipt(rpc, txHash, { pollIntervalMs: 500, timeoutMs: 60_000 });

  if (!receipt || receipt.status !== "SUCCESS") {
    throw new Error(`Deploy failed; receipt=${pretty(receipt)}`);
  }
  // Prefer receipt-provided address, fallback to derivation from signer payload.
  const addr = receipt.contractAddress || address.encodeAddress(signer.algId, await signer.addressPayload());
  return { address: addr, txHash };
}

async function callIncThenGet(SDK, rpc, chainId, signer, to) {
  const { tx, contracts } = SDK;
  const { manifest } = await loadManifestAndCode();

  const callTx = await tx.build.buildCall({
    chainId,
    senderPubkey: await signer.publicKeyBytes(),
    algId: signer.algId,
    to,
    function: "inc",
    args: [2]
  });
  const sig = await signer.sign(await tx.encode.signBytesForTx(callTx), "tx");
  const raw = await tx.build.attachSignature(callTx, sig);

  const txHash = await tx.send.sendRawTransaction(rpc, raw);
  const receipt = await tx.send.waitForReceipt(rpc, txHash, { pollIntervalMs: 500, timeoutMs: 60_000 });
  if (!receipt || receipt.status !== "SUCCESS") {
    throw new Error(`inc(2) failed; receipt=${pretty(receipt)}`);
  }

  const decoded = contracts.events.decodeReceiptEvents(manifest.abi, receipt);

  // Try a pure view path
  let valueAfter = undefined;
  try {
    valueAfter = await rpc.call("state.call", [{ to, function: "get", args: [] }]);
  } catch {
    // Fallback: send a tx (devnet)
    const viewTx = await tx.build.buildCall({
      chainId,
      senderPubkey: await signer.publicKeyBytes(),
      algId: signer.algId,
      to,
      function: "get",
      args: []
    });
    const vSig = await signer.sign(await tx.encode.signBytesForTx(viewTx), "tx");
    const vRaw = await tx.build.attachSignature(viewTx, vSig);
    const vHash = await tx.send.sendRawTransaction(rpc, vRaw);
    const vRcpt = await tx.send.waitForReceipt(rpc, vHash, { pollIntervalMs: 500, timeoutMs: 60_000 });
    valueAfter = vRcpt?.return?.value;
  }

  return { txHash, receipt, decodedEvents: decoded, valueAfterInc: valueAfter };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const SDK = await loadSdk();
  const { rpc: rpcNs, address } = SDK;

  const rpc = new rpcNs.http.HttpClient(args.rpc);

  const chainId = args.chain && args.chain > 0 ? args.chain : await detectChainId(rpc);
  const signer = await makeSigner(SDK, args, chainId);
  const senderAddr = address.encodeAddress(signer.algId, await signer.addressPayload());

  console.log("[e2e-ts] Using sender:", senderAddr);
  console.log("[e2e-ts] ChainId:", chainId);

  // Deploy
  const { address: contractAddr, txHash: deployHash } = await deployCounter(SDK, rpc, chainId, signer);
  console.log("[e2e-ts] Deployed contract address:", contractAddr);
  console.log("[e2e-ts] Deploy tx:", deployHash);

  // Call inc/get
  const res = await callIncThenGet(SDK, rpc, chainId, signer, contractAddr);
  console.log("[e2e-ts] inc(2) tx:", res.txHash);
  console.log("[e2e-ts] Value after inc:", res.valueAfterInc);

  const val = Number(res.valueAfterInc);
  if (!Number.isFinite(val) || val < 2) {
    console.error("[e2e-ts] ERROR: unexpected counter value:", res.valueAfterInc);
    process.exit(3);
  }

  if (res.decodedEvents?.length) {
    console.log("[e2e-ts] Events:");
    for (const ev of res.decodedEvents) console.log("  -", pretty(ev));
  }

  console.log("[e2e-ts] ✅ success");
}

main().catch((err) => {
  console.error("[e2e-ts] Fatal:", err);
  process.exit(1);
});
