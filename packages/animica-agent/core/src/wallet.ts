/**
 * Wallet adapter.
 *
 * The agent never signs or broadcasts silently. This adapter exposes:
 *   - identity resolution (which Animica address is "us")
 *   - read-only balance lookup via RPC (local node preferred)
 *   - a Signer interface that callers implement; default is a NoopSigner
 *     that refuses unless replaced by an interactive prompt (CLI) or by the
 *     wallet-extension bridge.
 *
 * The wallet identity flow is:
 *   1. cfg.walletMode === "off"       -> no wallet
 *   2. cfg.walletMode === "readonly"  -> identity must be supplied by user (cfg.minerAddress or arg)
 *   3. cfg.walletMode === "extension" -> defer to a bridge (out of scope for core)
 *   4. cfg.walletMode === "file"      -> read a JSON vault file (address only; never the key)
 */

import { existsSync, readFileSync } from "node:fs";

import type { AgentConfig } from "./config.js";
import { AgentError } from "./errors.js";
import { isLikelyAnimicaAddress, RpcClient } from "./rpc.js";
import { safeParse, toBigInt } from "./safe-json.js";

export interface WalletIdentity {
  address: string;
  label?: string;
  source: "config" | "file" | "extension" | "user";
}

export interface WalletBalance {
  address: string;
  raw: bigint;
  /** Decimal string in the smallest unit. */
  decimal: string;
  /** Human-readable ANM string with at most 6 fractional digits. */
  formattedANM: string;
  /** True if the node responded successfully. */
  reachable: boolean;
  error?: string;
}

const ANM_DECIMALS = 18;

export function formatANM(raw: bigint, fractional = 6): string {
  if (raw < 0n) return `-${formatANM(-raw, fractional)}`;
  const base = 10n ** BigInt(ANM_DECIMALS);
  const whole = raw / base;
  const frac = raw % base;
  if (fractional === 0) return whole.toString();
  const fracStr = frac.toString().padStart(ANM_DECIMALS, "0").slice(0, fractional).replace(/0+$/, "");
  return fracStr.length ? `${whole.toString()}.${fracStr}` : whole.toString();
}

export function parseANM(value: string): bigint {
  const v = value.trim();
  if (!v) throw new AgentError("WALLET", "empty ANM amount");
  const m = v.match(/^(\d+)(?:\.(\d+))?$/);
  if (!m) throw new AgentError("WALLET", `invalid ANM amount: ${value}`);
  const whole = BigInt(m[1]);
  const frac = (m[2] ?? "").padEnd(ANM_DECIMALS, "0").slice(0, ANM_DECIMALS);
  return whole * 10n ** BigInt(ANM_DECIMALS) + (frac ? BigInt(frac) : 0n);
}

export function resolveWalletIdentity(cfg: AgentConfig): WalletIdentity | null {
  if (cfg.walletMode === "off") return null;
  if (cfg.minerAddress && isLikelyAnimicaAddress(cfg.minerAddress)) {
    return { address: cfg.minerAddress, source: "config" };
  }
  if (cfg.walletMode === "file" && cfg.walletFile && existsSync(cfg.walletFile)) {
    try {
      const j = safeParse<Record<string, unknown>>(readFileSync(cfg.walletFile, "utf8"));
      const addr = (j.address ?? j.publicAddress) as string | undefined;
      const label = j.label as string | undefined;
      if (typeof addr === "string" && isLikelyAnimicaAddress(addr)) {
        return { address: addr, source: "file", label };
      }
    } catch {
      /* fall through; tolerate corrupt vault */
    }
  }
  return null;
}

export async function fetchBalance(rpcUrl: string, address: string, timeoutMs = 4000): Promise<WalletBalance> {
  const client = new RpcClient({ url: rpcUrl, timeoutMs });
  try {
    let hex: string;
    try {
      hex = await client.call<string>({
        method: "animica_getBalance",
        params: [address, "latest"],
      });
    } catch {
      hex = await client.call<string>({ method: "eth_getBalance", params: [address, "latest"] });
    }
    const raw = toBigInt(hex);
    return {
      address,
      raw,
      decimal: raw.toString(10),
      formattedANM: formatANM(raw),
      reachable: true,
    };
  } catch (err) {
    return {
      address,
      raw: 0n,
      decimal: "0",
      formattedANM: "0",
      reachable: false,
      error: (err as Error).message,
    };
  }
}

/* ---------------- Signer abstraction ---------------- */

export interface SignRequest {
  /** Plain payload to sign; the actual chain transaction is constructed by the wallet. */
  payload: { kind: string; data: unknown };
  /** Reason shown to the user before approval. */
  reason: string;
  /** Estimated maximum ANM the user could be charged. */
  estimatedCostRaw: bigint;
}

export interface SignedResult {
  signature?: string;
  /** Optional on-chain tx hash if the signer also submitted. */
  txHash?: string;
}

export interface Signer {
  readonly name: string;
  sign(req: SignRequest): Promise<SignedResult>;
}

export class NoopSigner implements Signer {
  public readonly name = "noop";
  async sign(_req: SignRequest): Promise<SignedResult> {
    throw new AgentError(
      "WALLET_LOCKED",
      "no signer is configured: re-run with a wallet (extension/file) or pass --no-pay to use the offline plan",
    );
  }
}

export class InteractiveSigner implements Signer {
  public readonly name = "interactive";
  constructor(
    private readonly prompt: (req: SignRequest) => Promise<boolean>,
    private readonly emit: (req: SignRequest) => Promise<SignedResult>,
  ) {}
  async sign(req: SignRequest): Promise<SignedResult> {
    const ok = await this.prompt(req);
    if (!ok) throw new AgentError("WALLET_DENIED", "user denied signing request");
    return this.emit(req);
  }
}
