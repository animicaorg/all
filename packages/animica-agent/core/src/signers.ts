/**
 * Real Signer implementations.
 *
 * Two production-shaped signers ship today:
 *
 *  - ExtensionSigner: browser-only. Talks to the existing Animica wallet
 *    extension via the injected `window.animica` provider. Mirrors the
 *    background message API in apps/wallet-extension/src/background:
 *      eth_requestAccounts | animica_chainId | personal_sign |
 *      animica_sendTransaction. It never holds keys.
 *
 *  - NodeWalletSigner: CLI-only. Delegates to the existing Python
 *    `animica tx send` command which already implements PQ signing,
 *    nonce handling, chain-id verification, and RPC submission. The
 *    keystore lives inside the Python CLI as it does today; we do not
 *    duplicate or move it.
 *
 * Both signers honor the Signer contract: they refuse silently-signed
 * transactions, surface SignedResult, and never leak secrets. They are
 * imported on demand so a Node-only consumer doesn't pay the cost of
 * fetching `window.animica` and a browser consumer doesn't accidentally
 * shell out to Python.
 */

import { spawnSync } from "node:child_process";

import type { Signer, SignRequest, SignedResult } from "./wallet.js";
import { AgentError } from "./errors.js";
import { formatANM } from "./wallet.js";

/* --------------------------------- ExtensionSigner --------------------------------- */

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

interface AnimicaProvider {
  request<T = unknown>(args: { method: string; params?: unknown[] }): Promise<T>;
}

export interface ExtensionSignerOptions {
  /** Optional override for the provider object. Useful for tests. */
  provider?: AnimicaProvider;
  /** Origin to surface to the extension; defaults to window.location.origin. */
  origin?: string;
}

export class ExtensionSigner implements Signer {
  public readonly name = "extension";
  private readonly opts: ExtensionSignerOptions;
  constructor(opts: ExtensionSignerOptions = {}) {
    this.opts = opts;
  }
  private provider(): AnimicaProvider {
    const p = this.opts.provider ?? (typeof globalThis !== "undefined" && (globalThis as unknown as Window).animica);
    if (!p || typeof p.request !== "function") {
      throw new AgentError(
        "SIGNER_UNAVAILABLE",
        "Animica wallet extension provider not detected. Install the extension and reload the page.",
      );
    }
    return p;
  }

  async requestAccounts(): Promise<string[]> {
    return this.provider().request<string[]>({ method: "animica_requestAccounts" });
  }

  async chainId(): Promise<bigint> {
    const r = await this.provider().request<string | number>({ method: "animica_chainId" });
    if (typeof r === "string") return r.startsWith("0x") ? BigInt(r) : BigInt(r);
    return BigInt(r);
  }

  async sign(req: SignRequest): Promise<SignedResult> {
    const p = this.provider();
    // The wallet extension owns the consent UI. We never bypass it.
    if (req.payload.kind === "agent-receipt") {
      // Use personal_sign so the user explicitly approves the receipt blob.
      const sig = await p.request<string>({
        method: "personal_sign",
        params: [JSON.stringify(req.payload.data), { reason: req.reason, estCost: formatANM(req.estimatedCostRaw) }],
      });
      return { signature: sig };
    }
    if (req.payload.kind === "anm-transfer") {
      const data = req.payload.data as { from: string; to: string; valueRaw: string | bigint };
      const valueHex = typeof data.valueRaw === "bigint" ? "0x" + data.valueRaw.toString(16) : data.valueRaw;
      const tx = await p.request<{ txHash?: string; hash?: string } | string>({
        method: "animica_sendTransaction",
        params: [
          {
            from: data.from,
            to: data.to,
            value: valueHex,
          },
        ],
      });
      const txHash = typeof tx === "string" ? tx : (tx.txHash ?? tx.hash);
      return { txHash };
    }
    throw new AgentError("SIGNER_KIND", `unsupported payload kind: ${req.payload.kind}`);
  }
}

/* --------------------------------- NodeWalletSigner --------------------------------- */

export interface NodeWalletSignerOptions {
  /** Override the python binary path. Defaults to .venv/bin/python under the repo or `python3`. */
  pythonBin?: string;
  /** Override the working directory. Defaults to repo root. */
  cwd?: string;
  /** Extra args appended to `animica tx send`. */
  extraArgs?: string[];
  /** Maximum time to wait for the python CLI in ms. */
  timeoutMs?: number;
  /**
   * Optional spawn shim for unit tests. Receives the resolved command, args,
   * and cwd; returns the simulated `child_process.spawnSync` result.
   */
  spawn?: (cmd: string, args: string[], opts: { cwd: string; timeout: number }) => {
    status: number | null;
    stdout?: string;
    stderr?: string;
  };
}

export type SignerFailureReason =
  | "insufficient-balance"
  | "bad-chain-id"
  | "wallet-not-found"
  | "nonce-conflict"
  | "rpc-unavailable"
  | "tx-rejected"
  | "tx-not-admitted"
  | "tx-not-confirmed"
  | "timeout"
  | "unknown";

export class SignerError extends AgentError {
  public readonly reason: SignerFailureReason;
  public readonly raw?: string;
  constructor(reason: SignerFailureReason, message: string, raw?: string) {
    super("SIGNER", message);
    this.name = "SignerError";
    this.reason = reason;
    this.raw = raw;
  }
}

/**
 * Classifies a `animica tx send` failure into a small set of operator-actionable
 * categories. The Python CLI prints distinctive strings that we match against
 * a short, ordered set of patterns. Order matters because a single error can
 * match more than one; we pick the most specific.
 */
export function classifyTxSendFailure(combined: string): SignerFailureReason {
  const s = combined.toLowerCase();
  if (/insufficient (?:balance|funds|spendable)/.test(s)) return "insufficient-balance";
  if (/(?:chain ?id|chainid).*mismatch|wrong chain/.test(s)) return "bad-chain-id";
  if (/wallet (?:not found|missing)|unknown wallet|no such wallet|account not found|key(?:store)? not found/.test(s)) return "wallet-not-found";
  if (/nonce (?:too low|conflict|mismatch|gap)|invalid nonce/.test(s)) return "nonce-conflict";
  if (/(?:connection (?:refused|reset)|econnrefused|rpc (?:unavailable|timeout|error)|cannot reach|fetch failed)/.test(s)) return "rpc-unavailable";
  if (/(?:not admitted|rejected by mempool|mempool full)/.test(s)) return "tx-not-admitted";
  if (/(?:tx (?:rejected|invalid)|signature (?:invalid|verify failed))/.test(s)) return "tx-rejected";
  if (/(?:wait[- ]timeout|did not confirm|no peer ack)/.test(s)) return "tx-not-confirmed";
  if (/etimedout|timeout/.test(s)) return "timeout";
  return "unknown";
}

/**
 * Parses the txHash from typical `animica tx send` output. Tolerates:
 *   - `txHash=0xabc…`
 *   - `tx_hash: 0xabc…`
 *   - JSON `{ "txHash": "0x…" }`
 *   - bare hash on its own line
 */
export function parseTxHash(combined: string): string | undefined {
  const m1 = combined.match(/tx[_]?hash["'\s:=]+(0x[0-9a-fA-F]{40,})/i);
  if (m1) return m1[1].toLowerCase();
  const m2 = combined.match(/^(0x[0-9a-fA-F]{40,})\s*$/m);
  if (m2) return m2[1].toLowerCase();
  return undefined;
}

export class NodeWalletSigner implements Signer {
  public readonly name = "node-wallet";
  constructor(private readonly opts: NodeWalletSignerOptions = {}) {}

  async sign(req: SignRequest): Promise<SignedResult> {
    if (req.payload.kind !== "anm-transfer") {
      // The Python tx.send only handles transfers. Receipt signing is not on-chain
      // here; consumers wanting a signed receipt should use the OfflineSettlement
      // path or ExtensionSigner. Refuse loudly rather than fabricate a value.
      throw new AgentError("SIGNER_KIND", `node-wallet signer only supports anm-transfer; got ${req.payload.kind}`);
    }
    const data = req.payload.data as { from: string; to: string; valueRaw: bigint };
    const python = this.opts.pythonBin ?? guessPythonBin();
    const cwd = this.opts.cwd ?? guessRepoRoot();
    const valueArg = formatANM(data.valueRaw, 18);
    const args = [
      "-m",
      "animica.cli.main",
      "tx",
      "send",
      "--from",
      data.from,
      "--to",
      data.to,
      "--value",
      valueArg,
      ...(this.opts.extraArgs ?? []),
    ];
    const spawnImpl = this.opts.spawn ?? ((c, a, o) => spawnSync(c, a, { ...o, encoding: "utf8" }));
    const r = spawnImpl(python, args, {
      cwd,
      timeout: this.opts.timeoutMs ?? 60_000,
    });
    const combined = (r.stdout ?? "") + "\n" + (r.stderr ?? "");
    if (r.status !== 0) {
      const reason = classifyTxSendFailure(combined);
      throw new SignerError(reason, `animica tx send failed (exit ${r.status}, ${reason}): ${combined.slice(0, 512).trim()}`, combined);
    }
    const txHash = parseTxHash(combined);
    if (!txHash) {
      // The CLI returned 0 but we cannot locate a hash. That is suspicious
      // enough to surface as not-admitted rather than fabricate success.
      throw new SignerError(
        "tx-not-admitted",
        `animica tx send returned exit 0 but no tx hash was found in output`,
        combined,
      );
    }
    return { txHash };
  }
}

function guessPythonBin(): string {
  try {
    const which = spawnSync("which", ["python3"], { encoding: "utf8" });
    if (which.status === 0 && which.stdout.trim()) return which.stdout.trim();
  } catch {
    /* ignore */
  }
  return "python3";
}

function guessRepoRoot(): string {
  // Walk up until a .git is found. Avoids depending on findRepoRoot to keep
  // this module standalone-importable.
  const path = require("node:path") as typeof import("node:path");
  const fs = require("node:fs") as typeof import("node:fs");
  let cur = process.cwd();
  for (let i = 0; i < 32; i++) {
    if (fs.existsSync(path.join(cur, ".git"))) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return process.cwd();
}
