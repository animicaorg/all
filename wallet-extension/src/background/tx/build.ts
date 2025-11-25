/**
 * Transaction builders for the Animica wallet extension.
 *
 * These helpers assemble well-typed TxBody objects for the three core kinds:
 *  - transfer
 *  - call
 *  - deploy
 *
 * Notes
 * -----
 * • Builders are *pure*: they only construct TxBody objects and perform light validation.
 *   Gas estimation / simulation should be handled by src/background/tx/simulate.ts.
 * • Amount-like values are strings (no floating points).
 * • Addresses are expected to be bech32m (anim1...) but we only soft-check here.
 */

import type { Bytes, TxBody, TxCall, TxDeploy, TxTransfer } from "./types";

const DEFAULTS = {
  // Conservative placeholders; simulation should refine before submission.
  gas: {
    transfer: 21000,
    call: 200_000,
    deploy: 2_000_000,
  },
  maxFee: "100000", // placeholder; UI should surface network-appropriate values
};

function assertPositiveInt(name: string, v: number) {
  if (!Number.isInteger(v) || v < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
}

function assertAmountString(name: string, v: string) {
  if (typeof v !== "string" || !/^[0-9]+$/.test(v)) {
    throw new Error(`${name} must be a decimal string (e.g. "1234")`);
  }
}

function softCheckAddress(addr: string) {
  if (typeof addr !== "string" || addr.length < 8) {
    throw new Error("address must be a string");
  }
  // Soft hint; strict validation should happen earlier via utils/bech32 if desired.
  if (!addr.startsWith("anim1")) {
    // Not fatal; just a gentle warning in dev logs.
    // eslint-disable-next-line no-console
    console.warn("Address does not look like bech32m anim1…:", addr);
  }
}

export interface CommonParams {
  chainId: number;
  from: string;
  nonce: number;
  gasLimit?: number; // optional; will default conservatively
  maxFee?: string; // optional decimal string; defaults conservatively
  memo?: string;
}

/* --------------------------------- Transfer -------------------------------- */

export interface TransferParams extends CommonParams {
  to: string;
  value: string; // decimal string
}

/** Build a transfer TxBody (pure, synchronous). */
export function buildTransfer(p: TransferParams): TxBody {
  if (p == null) throw new Error("params required");
  assertPositiveInt("chainId", p.chainId);
  softCheckAddress(p.from);
  softCheckAddress(p.to);
  assertPositiveInt("nonce", p.nonce);
  assertAmountString("value", p.value);

  const gasLimit = p.gasLimit ?? DEFAULTS.gas.transfer;
  assertPositiveInt("gasLimit", gasLimit);

  const maxFee = p.maxFee ?? DEFAULTS.maxFee;
  assertAmountString("maxFee", maxFee);

  const body: TxTransfer = {
    kind: "transfer",
    chainId: p.chainId,
    from: p.from,
    nonce: p.nonce,
    gasLimit,
    maxFee,
    to: p.to,
    value: p.value,
    memo: p.memo,
  };
  return body;
}

/* ----------------------------------- Call ---------------------------------- */

export interface CallParams extends CommonParams {
  to: string;
  data: Bytes; // ABI-encoded call payload
  value?: string; // optional decimal string (payable)
}

/** Build a contract call TxBody (pure, synchronous). */
export function buildCall(p: CallParams): TxBody {
  if (p == null) throw new Error("params required");
  assertPositiveInt("chainId", p.chainId);
  softCheckAddress(p.from);
  softCheckAddress(p.to);
  assertPositiveInt("nonce", p.nonce);

  if (!(p.data instanceof Uint8Array)) {
    throw new Error("data must be Uint8Array");
  }
  if (p.value != null) assertAmountString("value", p.value);

  const gasLimit = p.gasLimit ?? DEFAULTS.gas.call;
  assertPositiveInt("gasLimit", gasLimit);

  const maxFee = p.maxFee ?? DEFAULTS.maxFee;
  assertAmountString("maxFee", maxFee);

  const body: TxCall = {
    kind: "call",
    chainId: p.chainId,
    from: p.from,
    nonce: p.nonce,
    gasLimit,
    maxFee,
    to: p.to,
    data: p.data,
    value: p.value,
    memo: p.memo,
  };
  return body;
}

/* ---------------------------------- Deploy --------------------------------- */

export interface DeployParams extends CommonParams {
  code: Bytes; // contract bytecode / IR package payload (per chain rules)
  init?: Bytes; // optional constructor-args payload
}

/** Build a contract deploy TxBody (pure, synchronous). */
export function buildDeploy(p: DeployParams): TxBody {
  if (p == null) throw new Error("params required");
  assertPositiveInt("chainId", p.chainId);
  softCheckAddress(p.from);
  assertPositiveInt("nonce", p.nonce);

  if (!(p.code instanceof Uint8Array)) {
    throw new Error("code must be Uint8Array");
  }
  if (p.init != null && !(p.init instanceof Uint8Array)) {
    throw new Error("init must be Uint8Array when provided");
  }

  const gasLimit = p.gasLimit ?? DEFAULTS.gas.deploy;
  assertPositiveInt("gasLimit", gasLimit);

  const maxFee = p.maxFee ?? DEFAULTS.maxFee;
  assertAmountString("maxFee", maxFee);

  const body: TxDeploy = {
    kind: "deploy",
    chainId: p.chainId,
    from: p.from,
    nonce: p.nonce,
    gasLimit,
    maxFee,
    code: p.code,
    init: p.init,
    memo: p.memo,
  };
  return body;
}

/* ----------------------------- Convenience API ----------------------------- */

/**
 * Patch an existing TxBody with new gas/fee/memo (handy after simulation).
 */
export function withGasAndFee<T extends TxBody>(
  body: T,
  gasLimit: number,
  maxFee: string,
  memo?: string
): T {
  assertPositiveInt("gasLimit", gasLimit);
  assertAmountString("maxFee", maxFee);
  return {
    ...(body as any),
    gasLimit,
    maxFee,
    ...(memo !== undefined ? { memo } : {}),
  } as T;
}

/**
 * Quick defaults (useful for UIs before simulation refines values).
 */
export const DefaultGas = DEFAULTS.gas;
export const DefaultMaxFee = DEFAULTS.maxFee;
