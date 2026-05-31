// Animica node wrapper.
//
// Two layers:
//   - Read-only: state.* / chain.* RPC for balances, address validation,
//     tx receipts, confirmation counts.
//   - Mutation: shells out to the `animica` CLI to broadcast a signed
//     wallet.send tx. Mirrors how the chat bridge + buy-sol stack already
//     ship ANM, so credentials live in `~/.animica/wallets.json` once.
//
// Address validation only checks bech32 well-formedness — we don't try
// to enforce checksum here because the chain returns a "not_an_address"
// error if it's wrong anyway, and we want to reject obvious typos as
// fast as possible without a wider bech32 dependency.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { env } from "./env";

const exec = promisify(execFile);

// ── RPC helpers ────────────────────────────────────────────────────────

async function rpc<T>(method: string, params: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const e = env();
  if (e.ANM_RPC_USER && e.ANM_RPC_PASSWORD) {
    const cred = Buffer.from(`${e.ANM_RPC_USER}:${e.ANM_RPC_PASSWORD}`).toString("base64");
    headers.Authorization = `Basic ${cred}`;
  }
  const res = await fetch(e.ANM_RPC_URL, {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`anm_rpc ${method} → ${res.status}`);
  const body = (await res.json()) as { result?: T; error?: { message?: string } };
  if (body.error) throw new Error(`anm_rpc ${method}: ${body.error.message ?? "error"}`);
  if (body.result === undefined) throw new Error(`anm_rpc ${method}: no result`);
  return body.result;
}

// ── Address validation ────────────────────────────────────────────────

const BECH32_RE = /^anim1[0-9a-z]{40,80}$/;

export function validateAddress(addr: string): { ok: boolean; reason?: string } {
  if (!addr || typeof addr !== "string") return { ok: false, reason: "missing" };
  const trimmed = addr.trim();
  if (!BECH32_RE.test(trimmed)) {
    return { ok: false, reason: "Address must be a bech32 anim1… string." };
  }
  return { ok: true };
}

// ── Balance ───────────────────────────────────────────────────────────

const NANOS_PER_ANM = 1_000_000_000n;

interface BalanceResult {
  confirmed?: string;
  available?: string;
  balance?: string;
}

/** Returns whole-token balance as a decimal string ("12345.123456789"). */
export async function getBalance(address: string): Promise<string> {
  const raw = await rpc<string | BalanceResult>("state.getBalance", { address });
  let nanos: bigint;
  if (typeof raw === "string") {
    nanos = raw.startsWith("0x") ? BigInt(raw) : BigInt(raw);
  } else {
    const v = raw.confirmed ?? raw.available ?? raw.balance ?? "0";
    nanos = typeof v === "string" && v.startsWith("0x") ? BigInt(v) : BigInt(v);
  }
  const whole = nanos / NANOS_PER_ANM;
  const frac = (nanos % NANOS_PER_ANM).toString().padStart(9, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

export function getHotWalletBalance(): Promise<string> {
  return getBalance(env().ANM_HOT_WALLET_ADDRESS);
}

export function getDepositMasterBalance(): Promise<string> {
  return getBalance(env().ANM_DEPOSIT_MASTER_ADDRESS);
}

// ── Tx / confirmations ────────────────────────────────────────────────

export interface TxReceipt {
  blockNumber?: number;
  status?: string;
  logs?: unknown[];
  from?: string;
  to?: string;
  value?: string;
}

export async function getTransaction(txid: string): Promise<TxReceipt | null> {
  try {
    return await rpc<TxReceipt>("tx.getReceipt", [txid]);
  } catch {
    return null;
  }
}

export async function getConfirmations(txid: string): Promise<number> {
  const receipt = await getTransaction(txid);
  if (!receipt || receipt.blockNumber == null) return 0;
  try {
    const head = await rpc<{ height: number }>("chain.getHead", {});
    return Math.max(0, head.height - receipt.blockNumber + 1);
  } catch {
    return 0;
  }
}

// ── Hot-wallet send (CLI) ─────────────────────────────────────────────

/**
 * Broadcast an ANM payout from the hot wallet to `to`. `amountAnm` is a
 * decimal string ("10.5" = 10.5 ANM). Returns the broadcast tx hash.
 *
 * Idempotency: callers MUST check the order isn't already in ANM_SENT /
 * COMPLETE before calling — this function does not maintain that state.
 */
export async function sendANM(to: string, amountAnm: string): Promise<string> {
  const check = validateAddress(to);
  if (!check.ok) throw new Error(`anm.send: bad address (${check.reason})`);
  if (!/^\d+(\.\d{1,9})?$/.test(amountAnm)) {
    throw new Error(`anm.send: amount must be a positive decimal with ≤9 dp`);
  }
  const e = env();
  const args = [
    "wallet",
    "send",
    "--from",
    e.ANM_HOT_WALLET_LABEL,
    "--to",
    to,
    "--amount",
    amountAnm,
    "--unit",
    "anm",
    "--yes",
    "--json",
  ];
  const { stdout } = await exec(e.ANIMICA_CLI, args, {
    timeout: 60_000,
    env: { ...process.env, NO_COLOR: "1" },
  });
  let parsed: { tx_hash?: string; txid?: string; hash?: string };
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new Error(`anm.send: CLI returned non-JSON: ${stdout.slice(0, 200)}`);
  }
  const hash = parsed.tx_hash ?? parsed.txid ?? parsed.hash;
  if (!hash) throw new Error("anm.send: CLI returned no tx_hash");
  return hash;
}

// ── Deposit scanning ──────────────────────────────────────────────────

/**
 * Look back N blocks on the deposit master address and return any
 * incoming transfers. Used by the worker to detect SELL orders'
 * inbound ANM. The worker is responsible for matching txs to orders
 * (by sender address or memo).
 */
export interface Deposit {
  txid: string;
  from: string;
  amountAnm: string;
  confirmations: number;
  blockNumber: number;
}

export async function watchDeposits(
  toAddress: string,
  blocksBack = 20,
): Promise<Deposit[]> {
  try {
    const head = await rpc<{ height: number }>("chain.getHead", {});
    const fromBlock = Math.max(0, head.height - blocksBack);
    const txs = await rpc<Array<{
      hash?: string;
      txid?: string;
      from?: string;
      to?: string;
      value?: string;
      blockNumber?: number;
    }>>("state.getIncomingTransfers", {
      address: toAddress,
      fromBlock,
      toBlock: head.height,
    }).catch(() => null);
    if (!txs) return [];
    return txs
      .filter((t) => t.to === toAddress)
      .map((t) => {
        const nanos = BigInt(t.value ?? "0");
        const whole = nanos / NANOS_PER_ANM;
        const frac = (nanos % NANOS_PER_ANM).toString().padStart(9, "0").replace(/0+$/, "");
        return {
          txid: t.hash ?? t.txid ?? "",
          from: t.from ?? "",
          amountAnm: frac ? `${whole}.${frac}` : `${whole}`,
          confirmations: head.height - (t.blockNumber ?? 0) + 1,
          blockNumber: t.blockNumber ?? 0,
        };
      });
  } catch {
    return [];
  }
}

/**
 * Generate a fresh per-order deposit wallet on the Animica node and
 * return its bech32 address. The wallet's label is bound to the order
 * id so the worker can later sweep funds out via `sweepDepositToReserve`.
 *
 * Uses `animica wallet create --label gateway-deposit-<orderId>` under
 * the hood — credentials live in ~/.animica/wallets.json alongside the
 * hot wallet (same machine, same store).
 */
export async function createDepositWallet(orderId: string): Promise<string> {
  const e = env();
  const label = `gateway-deposit-${orderId.slice(0, 16)}`;
  const { stdout } = await exec(
    e.ANIMICA_CLI,
    ["wallet", "create", "--label", label, "--alg", "ml_dsa_65"],
    { timeout: 60_000, env: { ...process.env, NO_COLOR: "1" } },
  );
  const m = stdout.match(/Address:\s+(anim1[0-9a-z]+)/);
  if (!m) throw new Error(`anm.createDepositWallet: unable to parse address from CLI output`);
  return m[1];
}

/**
 * Sweep deposited ANM from a per-order wallet to the gateway's main
 * reserve. Called after the SELL order is confirmed so user funds
 * funnel into the hot wallet that backs future BUY payouts.
 *
 * Leaves a small dust amount in the per-order wallet to keep it
 * spendable without needing to top up the fee buffer.
 */
export async function sweepDepositToReserve(opts: {
  orderId: string;
  toAddress: string;
  amountAnm: string;
}): Promise<string> {
  const e = env();
  const label = `gateway-deposit-${opts.orderId.slice(0, 16)}`;
  // Leave a tiny dust amount (0.001 ANM) behind so the wallet stays
  // valid for retries / dust sweeps later.
  const dust = "0.001";
  const sweepable = (Number(opts.amountAnm) - Number(dust)).toFixed(9);
  if (Number(sweepable) <= 0) {
    throw new Error(`anm.sweep: nothing to sweep (amount ${opts.amountAnm} ≤ dust ${dust})`);
  }
  const { stdout } = await exec(
    e.ANIMICA_CLI,
    [
      "wallet",
      "send",
      "--from",
      label,
      "--to",
      opts.toAddress,
      "--amount",
      sweepable,
      "--unit",
      "anm",
      "--yes",
      "--json",
    ],
    { timeout: 60_000, env: { ...process.env, NO_COLOR: "1" } },
  );
  const parsed = JSON.parse(stdout);
  const hash = parsed.tx_hash ?? parsed.txid ?? parsed.hash;
  if (!hash) throw new Error("anm.sweep: CLI returned no tx_hash");
  return hash;
}

/**
 * Legacy single-address path. Falls back to a static master address
 * for any code that hasn't been migrated to the per-order wallet flow
 * yet.
 */
export function assignDepositAddress(_orderId: string): string {
  return env().ANM_DEPOSIT_MASTER_ADDRESS;
}
