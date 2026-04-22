import type { ModelPricing } from './types.js';

export const ANM_NANOS = 1_000_000_000n;
export const BPS = 10_000n;

export type ChargeBreakdown = {
  requestBaseAnmNanos: bigint;
  inputChargeAnmNanos: bigint;
  outputChargeAnmNanos: bigint;
  embeddingChargeAnmNanos: bigint;
  grossChargeAnmNanos: bigint;
  subsidyAnmNanos: bigint;
  netChargeAnmNanos: bigint;
  providerRewardAnmNanos: bigint;
  treasuryCutAnmNanos: bigint;
};

export function parseAnmNanos(value: string | number | bigint): bigint {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') return BigInt(Math.trunc(value));
  const clean = value.trim();
  if (!/^[-]?\d+$/.test(clean)) {
    throw new Error(`Invalid ANM nanos value: ${value}`);
  }
  return BigInt(clean);
}

export function formatAnmNanos(value: bigint): string {
  return value.toString(10);
}

export function formatAnm(value: bigint): string {
  const sign = value < 0n ? '-' : '';
  const abs = value < 0n ? -value : value;
  const whole = abs / ANM_NANOS;
  const frac = (abs % ANM_NANOS).toString().padStart(9, '0').replace(/0+$/, '');
  return frac.length > 0 ? `${sign}${whole}.${frac}` : `${sign}${whole}`;
}

export function toAnmNanosFromDecimal(input: string): bigint {
  const value = input.trim();
  if (!/^\d+(\.\d{1,9})?$/.test(value)) {
    throw new Error(`Invalid ANM decimal amount: ${input}`);
  }
  const [whole, fraction = ''] = value.split('.');
  const frac = (fraction + '000000000').slice(0, 9);
  return BigInt(whole) * ANM_NANOS + BigInt(frac);
}

export function calculateCharge(input: {
  pricing: ModelPricing;
  inputTokens?: number;
  outputTokens?: number;
  embeddingVectors?: number;
  subsidyBps?: number;
}): ChargeBreakdown {
  const inputTokens = BigInt(Math.max(0, Math.trunc(input.inputTokens ?? 0)));
  const outputTokens = BigInt(Math.max(0, Math.trunc(input.outputTokens ?? 0)));
  const embeddingVectors = BigInt(Math.max(0, Math.trunc(input.embeddingVectors ?? 0)));
  const subsidyBps = BigInt(Math.max(0, Math.min(10_000, Math.trunc(input.subsidyBps ?? 0))));

  const requestBaseAnmNanos = parseAnmNanos(input.pricing.requestBaseAnmNanos);
  const inputChargeAnmNanos = inputTokens * parseAnmNanos(input.pricing.inputTokenAnmNanos);
  const outputChargeAnmNanos = outputTokens * parseAnmNanos(input.pricing.outputTokenAnmNanos);
  const embeddingChargeAnmNanos = embeddingVectors * parseAnmNanos(input.pricing.embeddingVectorAnmNanos);

  const grossChargeAnmNanos =
    requestBaseAnmNanos + inputChargeAnmNanos + outputChargeAnmNanos + embeddingChargeAnmNanos;
  const subsidyAnmNanos = (grossChargeAnmNanos * subsidyBps) / BPS;
  const netChargeAnmNanos = grossChargeAnmNanos - subsidyAnmNanos;

  const providerShareBps = BigInt(input.pricing.providerShareBps);
  const treasuryShareBps = BigInt(input.pricing.treasuryShareBps);
  if (providerShareBps + treasuryShareBps !== BPS) {
    throw new Error('Invalid pricing split, providerShareBps + treasuryShareBps must equal 10000');
  }

  const providerRewardAnmNanos = (netChargeAnmNanos * providerShareBps) / BPS;
  const treasuryCutAnmNanos = netChargeAnmNanos - providerRewardAnmNanos;

  return {
    requestBaseAnmNanos,
    inputChargeAnmNanos,
    outputChargeAnmNanos,
    embeddingChargeAnmNanos,
    grossChargeAnmNanos,
    subsidyAnmNanos,
    netChargeAnmNanos,
    providerRewardAnmNanos,
    treasuryCutAnmNanos
  };
}

export function ensureBudget(maxBudgetAnmNanos: bigint, expectedChargeAnmNanos: bigint): void {
  if (expectedChargeAnmNanos > maxBudgetAnmNanos) {
    throw new Error('Job would exceed max ANM budget');
  }
}

export function deterministicPseudoEmbedding(text: string, dimensions = 64): number[] {
  const out = new Array<number>(dimensions);
  let state = 0x9e3779b9;
  for (let i = 0; i < text.length; i += 1) {
    state ^= text.charCodeAt(i) + ((state << 6) >>> 0) + (state >>> 2);
  }
  for (let i = 0; i < dimensions; i += 1) {
    state = (state * 1664525 + 1013904223) >>> 0;
    out[i] = (state / 0xffffffff) * 2 - 1;
  }
  return out;
}

export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}
