export const ANM_NANOS_PER_ANM = 1_000_000_000n;

export function toBigIntNanos(value: bigint | number | string): bigint {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') return BigInt(Math.trunc(value));
  const trimmed = value.trim();
  if (!trimmed) return 0n;
  return BigInt(trimmed);
}

export function formatAnmNanos(value: bigint | number | string, fractionDigits = 6): string {
  const nanos = toBigIntNanos(value);
  const negative = nanos < 0n;
  const abs = negative ? -nanos : nanos;
  const whole = abs / ANM_NANOS_PER_ANM;
  const fraction = abs % ANM_NANOS_PER_ANM;
  const rawFraction = fraction.toString().padStart(9, '0').slice(0, Math.max(0, Math.min(9, fractionDigits)));
  const fractionPart = rawFraction.replace(/0+$/g, '');
  return `${negative ? '-' : ''}${whole.toString()}${fractionPart ? `.${fractionPart}` : ''} ANM`;
}

export function estimateTokenCount(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return Math.max(1, Math.ceil(trimmed.length / 4));
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function shortAddress(address: string, size = 6): string {
  const trimmed = address.trim();
  if (trimmed.length <= size * 2 + 2) {
    return trimmed;
  }
  return `${trimmed.slice(0, size + 2)}...${trimmed.slice(-size)}`;
}
