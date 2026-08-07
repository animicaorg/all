// Animica Python Cloud — REAL on-chain deployment anchoring (§61).
//
// VERIFIED TRUTH about what Animica consensus does and does not do (probed against the live
// node before this file was written — do not "improve" these claims):
//   * da.put / da.get work: the DA layer is a writable, content-addressed blob store
//     (blob_id = sha3-256 hex of the bytes, 32 MiB cap per blob).
//   * DEPLOY (t=1) transactions succeed and are consensus-carried. CALL transactions into
//     vm_py REVERT on mainnet — raw Python execution is fail-closed at the node (enabling it
//     would be node RCE). Therefore a Python Cloud deployment is ANCHORED on-chain and
//     EXECUTED off-chain. Never state or imply that consensus executes the Python.
//
// The anchor is two artifacts:
//   1. a DA blob carrying the full canonical artifact bundle (manifest + verbatim source), so
//      anyone with the blob id can re-hash and verify what was deployed, and
//   2. a DEPLOY tx whose package manifest binds {kind:"animica.pythoncloud.v1", owner,
//      function, version, sourceSha3, artifactSha3, daBlobId, ts}.
//
// Sign+submit path: the animica CLI (`animica contract deploy`), spawned with the ABSOLUTE
// binary path and ANIMICA_WALLETS_FILE — the exact idiom lib/send.ts proved in production
// (systemd PATH does not contain `animica`). The CLI compiles the tiny carrier source, wraps
// it with our sidecar manifest.json into the deploy package, signs with the anchor wallet
// (ML-DSA-65) and submits.
//
// HONESTY RULE: a txid in this module comes from CLI/node output or it does not exist. A
// broadcast that cannot happen (missing wallet, zero balance, CLI failure) returns
// {broadcast:false, reason} and the deployment records exactly that.

import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { config } from '../config';
import { getAddressBalance, getHead, getReceipt, getTxStatus } from '../chain';
import { runtime } from './config';

// ---------------------------------------------------------------------------
// DA blob (JSON-RPC da.put / da.get against the local node)
// ---------------------------------------------------------------------------

// Hard node-side cap is 32 MiB; anchoring payloads are source bundles (≤ maxSourceBytes plus
// manifest overhead) so anything near the cap indicates a caller bug, not a big deploy.
export const DA_MAX_BLOB_BYTES = 32 * 1024 * 1024;

let rpcId = 1;
async function nodeRpc<T = any>(method: string, params: any[], timeoutMs = 30_000): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(config.rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`rpc ${method} http ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(`rpc ${method}: ${data.error.message ?? JSON.stringify(data.error)}`);
    return data.result as T;
  } finally {
    clearTimeout(t);
  }
}

function sha3hex(bytes: Buffer): string {
  return createHash('sha3-256').update(bytes).digest('hex');
}

/**
 * Store one anchoring payload in the DA layer. Content-addressed, so re-putting identical
 * bytes returns the same blob id — which is what makes deployVersion safely resumable.
 */
export async function daPutAnchor(
  bytes: Buffer,
  meta: { contentType: string; owner: string; tags: string[] },
): Promise<{ blobId: string; sizeBytes: number }> {
  if (bytes.length === 0) throw new Error('refusing to anchor an empty payload');
  if (bytes.length > DA_MAX_BLOB_BYTES) {
    throw new Error(`anchor payload is ${bytes.length} bytes; the DA blob cap is ${DA_MAX_BLOB_BYTES}`);
  }
  const r = await nodeRpc<any>('da.put', [
    {
      bytes: bytes.toString('base64'),
      metadata: { content_type: meta.contentType, owner: meta.owner, tags: meta.tags },
    },
  ]);
  const blobId = r?.blob_id ?? r?.blobId ?? r?.id;
  if (!blobId) throw new Error(`da.put returned no blob id: ${JSON.stringify(r).slice(0, 300)}`);
  // The store is content-addressed; verify the node agrees with our own hash so a node-side
  // regression can never silently anchor different bytes than we committed to.
  const expected = sha3hex(bytes);
  if (String(blobId).toLowerCase() !== expected) {
    throw new Error(`da.put content-address mismatch: node=${blobId} local=${expected}`);
  }
  return { blobId: String(blobId), sizeBytes: Number(r?.size_bytes ?? bytes.length) };
}

/** Fetch an anchored blob back, verifying it hashes to its id (provenance UI + audits). */
export async function daGetAnchor(blobId: string): Promise<Buffer> {
  const r = await nodeRpc<any>('da.get', [{ blob_id: blobId }]);
  const b64 = r?.bytes;
  if (typeof b64 !== 'string') throw new Error(`da.get returned no bytes for ${blobId}`);
  const buf = Buffer.from(b64, 'base64');
  if (sha3hex(buf) !== blobId.toLowerCase()) {
    throw new Error(`DA blob ${blobId} failed content-hash verification`);
  }
  return buf;
}

// ---------------------------------------------------------------------------
// Anchor wallet
// ---------------------------------------------------------------------------

export interface AnchorWallet {
  label: string;
  address: string;
  algName: string;
}

/**
 * Resolve the platform anchor wallet (runtime.anchorWalletLabel) from the CLI wallets file.
 *
 * We read ONLY label/address/alg metadata — secret material never leaves the file; signing
 * happens inside the animica CLI process, exactly like lib/send.ts payouts. `wallet list` has
 * no --json flag, so the wallets file (the CLI's own store) is the machine-readable source.
 */
export async function anchorWallet(): Promise<AnchorWallet | null> {
  let raw: string;
  try {
    raw = await readFile(config.walletsFile, 'utf8');
  } catch {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    const list: any[] = Array.isArray(parsed?.wallets) ? parsed.wallets : Array.isArray(parsed) ? parsed : [];
    const hit = list.find((w) => w && w.label === runtime.anchorWalletLabel && typeof w.address === 'string');
    if (!hit) return null;
    return { label: String(hit.label), address: String(hit.address), algName: String(hit.alg_name ?? '') };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Anchor broadcast (DEPLOY t=1 via the animica CLI)
// ---------------------------------------------------------------------------

/** The binding committed into the DEPLOY tx's package manifest. */
export interface AnchorManifest {
  kind: 'animica.pythoncloud.v1';
  /** Developer's Animica address (bech32m). */
  owner: string;
  /** `{ownerHandleOrAddress}/{slug}` — the public function identity. */
  function: string;
  version: number;
  sourceSha3: string;
  artifactSha3: string;
  daBlobId: string;
  /** ms since epoch, at broadcast time. */
  ts: number;
}

export interface BroadcastResult {
  broadcast: boolean;
  /** Present ONLY when the CLI actually submitted a tx. Never synthesized. */
  txid: string | null;
  deployerAddress: string | null;
  /** Honest, user-showable reason when broadcast=false. */
  reason: string | null;
}

// The carrier contract the DEPLOY tx compiles and carries. Deliberately constant: the anchor's
// meaning lives in the manifest binding, and a constant body keeps the tx small and the
// compiled IR deterministic across every deployment.
const CARRIER_SOURCE = `# Animica Python Cloud deployment anchor.
# This contract exists to carry the deployment manifest binding on-chain; the function itself
# is ANCHORED here and EXECUTED off-chain by the Animica Python Cloud (consensus does not
# execute Python CALLs — vm_py raw execution is fail-closed on mainnet by design).
def manifest(request):
    return "animica.pythoncloud.v1"
`;

const CLI_TIMEOUT_MS = Number(process.env.CLOUD_ANCHOR_CLI_TIMEOUT_MS ?? '60000');

function runCli(args: string[], timeoutMs: number): Promise<{ code: number | null; stdout: string; stderr: string; timedOut: boolean }> {
  return new Promise((resolve) => {
    const child = spawn(config.cli, args, {
      env: { ...process.env, ANIMICA_WALLETS_FILE: config.walletsFile, ANIMICA_RPC_URL: config.rpcUrl },
    });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const killer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill('SIGKILL');
      } catch {
        /* already gone */
      }
    }, timeoutMs);
    child.stdout.on('data', (d) => (stdout += d.toString()));
    child.stderr.on('data', (d) => (stderr += d.toString()));
    child.on('error', (e) => {
      clearTimeout(killer);
      resolve({ code: null, stdout, stderr: `spawn failed: ${e.message}`, timedOut });
    });
    child.on('close', (code) => {
      clearTimeout(killer);
      resolve({ code, stdout, stderr, timedOut });
    });
  });
}

/**
 * Broadcast the anchor DEPLOY tx for one deployment.
 *
 * Pre-flight: the anchor wallet must exist AND hold a nonzero balance — a zero-balance wallet
 * cannot pay gas, so the tx would never land. In that case we return broadcast:false with the
 * real reason; the deployment still goes ACTIVE, honestly recorded as unanchored (§61: the
 * platform never fabricates a txid to look healthy).
 */

/**
 * Can a deploy anchor on-chain right now, and if not, why?
 *
 * Read-only: it resolves the platform anchor wallet and reads its balance, and
 * broadcasts nothing. `broadcastAnchor` applies exactly these two conditions at
 * deploy time, so a preview built from this cannot promise something the deploy
 * will then refuse — the reason strings are deliberately the same wording.
 *
 * Added because `/api/cloud/v1/estimate` did not return an `anchor` block while
 * the deploy editor read `estimateRes.anchor.willBroadcast` unconditionally. That
 * is a TypeError on undefined, which React surfaces as the bare
 * "Application error: a client-side exception has occurred" — so pressing
 * Estimate cost took the whole page down.
 */
export async function anchorReadiness(): Promise<{
  enabled: boolean;
  walletAddress: string | null;
  walletBalanceNanm: string | null;
  willBroadcast: boolean;
  reason: string | null;
}> {
  const wallet = await anchorWallet();
  if (!wallet) {
    return {
      enabled: false,
      walletAddress: null,
      walletBalanceNanm: null,
      willBroadcast: false,
      reason: `anchor wallet label "${runtime.anchorWalletLabel}" was not found in the platform wallet store; deployment is unanchored`,
    };
  }
  let balanceNanm: bigint | null = null;
  try {
    balanceNanm = (await getAddressBalance(wallet.address)).nanm;
  } catch (e: any) {
    return {
      enabled: true,
      walletAddress: wallet.address,
      walletBalanceNanm: null,
      willBroadcast: false,
      reason: `could not read the anchor wallet balance (${String(e?.message ?? e).slice(0, 200)}); deployment is unanchored`,
    };
  }
  if (balanceNanm <= 0n) {
    return {
      enabled: true,
      walletAddress: wallet.address,
      walletBalanceNanm: balanceNanm.toString(),
      willBroadcast: false,
      reason: 'the platform anchor wallet holds no ANM, so it cannot pay gas; deployment is unanchored',
    };
  }
  return {
    enabled: true,
    walletAddress: wallet.address,
    walletBalanceNanm: balanceNanm.toString(),
    willBroadcast: true,
    reason: null,
  };
}

export async function broadcastAnchor(manifest: AnchorManifest): Promise<BroadcastResult> {
  const wallet = await anchorWallet();
  if (!wallet) {
    return {
      broadcast: false,
      txid: null,
      deployerAddress: null,
      reason: `anchor wallet label "${runtime.anchorWalletLabel}" was not found in the platform wallet store; deployment is unanchored`,
    };
  }

  let balanceNanm: bigint;
  try {
    balanceNanm = (await getAddressBalance(wallet.address)).nanm;
  } catch (e: any) {
    return {
      broadcast: false,
      txid: null,
      deployerAddress: wallet.address,
      reason: `could not read the anchor wallet balance (${String(e?.message ?? e).slice(0, 200)}); deployment is unanchored`,
    };
  }
  if (balanceNanm <= 0n) {
    return {
      broadcast: false,
      txid: null,
      deployerAddress: wallet.address,
      reason: `anchor wallet ${wallet.address.slice(0, 20)}… has zero balance and cannot pay gas; deployment is unanchored until the platform funds it`,
    };
  }

  // Stage the carrier source + sidecar manifest.json. `animica contract deploy <src.py>`
  // discovers manifest.json in the source's directory (contracts/artifacts.py
  // resolve_manifest_for_source) and binds it — with the source text — into the deploy package.
  let workDir: string | null = null;
  try {
    workDir = await mkdtemp(path.join(tmpdir(), 'anm-anchor-'));
    const srcPath = path.join(workDir, 'anchor.py');
    await writeFile(srcPath, CARRIER_SOURCE, 'utf8');
    await writeFile(
      path.join(workDir, 'manifest.json'),
      JSON.stringify(
        {
          // `name` keys the CLI's artifact bookkeeping; make it unique per anchor so nothing collides.
          name: `pycloud-anchor-v${manifest.version}-${manifest.artifactSha3.slice(0, 12)}`,
          manifestVersion: 1,
          kind: manifest.kind,
          owner: manifest.owner,
          function: manifest.function,
          version: manifest.version,
          sourceSha3: manifest.sourceSha3,
          artifactSha3: manifest.artifactSha3,
          daBlobId: manifest.daBlobId,
          ts: manifest.ts,
        },
        null,
        2,
      ),
      'utf8',
    );

    // --no-wait: submission is the slow-signature part; inclusion is confirmAnchor's job, so
    // the deploy pipeline is never held hostage to block cadence inside this call.
    const r = await runCli(
      ['contract', 'deploy', srcPath, '--from', wallet.label, '--gas-price', '1', '--no-wait', '--json'],
      CLI_TIMEOUT_MS,
    );
    if (r.timedOut) {
      return {
        broadcast: false,
        txid: null,
        deployerAddress: wallet.address,
        reason: `anchor broadcast timed out after ${CLI_TIMEOUT_MS}ms; deployment is unanchored`,
      };
    }
    if (r.code !== 0) {
      const detail = (r.stderr || r.stdout || `exit ${r.code}`).trim().replace(/\s+/g, ' ').slice(0, 400);
      return {
        broadcast: false,
        txid: null,
        deployerAddress: wallet.address,
        reason: `anchor broadcast failed: ${detail}`,
      };
    }

    // --json emits exactly one JSON object on stdout; parse it, with a brace-scan fallback in
    // case a warning line ever precedes it.
    let payload: any = null;
    try {
      payload = JSON.parse(r.stdout);
    } catch {
      const start = r.stdout.indexOf('{');
      if (start >= 0) {
        try {
          payload = JSON.parse(r.stdout.slice(start));
        } catch {
          payload = null;
        }
      }
    }
    const txid = typeof payload?.tx_hash === 'string' && /^0x[0-9a-fA-F]{16,}$/.test(payload.tx_hash) ? payload.tx_hash : null;
    if (!txid) {
      return {
        broadcast: false,
        txid: null,
        deployerAddress: wallet.address,
        reason: `anchor CLI succeeded but returned no tx hash (${r.stdout.trim().slice(0, 200) || 'empty output'})`,
      };
    }
    return { broadcast: true, txid, deployerAddress: String(payload?.deployer ?? wallet.address), reason: null };
  } finally {
    if (workDir) await rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Confirmation
// ---------------------------------------------------------------------------

export interface AnchorConfirmation {
  confirmed: boolean;
  /** Block height the anchor tx was included at, or null while still in the mempool. */
  height: number | null;
  /** head.height - blockNumber; 0 while unincluded. */
  confirms: number;
}

/**
 * Poll the anchor tx toward finality: confirmed once it has a blockNumber and
 * head.height - blockNumber >= runtime.anchorConfirmations (12 — the platform-wide finality
 * depth). Bounded by maxWaitMs; pass 0 for a single-shot status read. Never throws on RPC
 * hiccups — an unreachable node just means "not confirmed yet".
 */
export async function confirmAnchor(
  txid: string,
  opts: { maxWaitMs?: number; pollMs?: number } = {},
): Promise<AnchorConfirmation> {
  const maxWaitMs = opts.maxWaitMs ?? 120_000;
  const pollMs = Math.max(1_000, opts.pollMs ?? 3_000);
  const deadline = Date.now() + maxWaitMs;

  let height: number | null = null;
  let confirms = 0;

  for (;;) {
    try {
      const st = await getTxStatus(txid);
      height = st?.blockNumber ?? null;
      if (height == null) {
        // Some node builds surface inclusion on the receipt before the status view.
        const rc = await getReceipt(txid);
        const bn = rc?.blockNumber ?? rc?.block_number ?? rc?.block_height ?? null;
        height = bn != null ? Number(bn) : null;
      }
      if (height != null) {
        const head = await getHead();
        confirms = Math.max(0, head.height - height);
        if (confirms >= runtime.anchorConfirmations) return { confirmed: true, height, confirms };
      }
    } catch {
      // Node briefly unreachable: report the truth so far rather than failing the deployment.
    }
    if (Date.now() >= deadline) return { confirmed: false, height, confirms };
    await new Promise((r) => setTimeout(r, Math.min(pollMs, Math.max(1, deadline - Date.now()))));
  }
}
