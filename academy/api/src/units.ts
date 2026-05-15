// 1 ANIMICA = 1_000_000_000 base units (9 decimals).
export const ANIMICA_DECIMALS = 9;
export const ONE_ANIM = 1_000_000_000n;

export function animToBaseUnits(input: string): bigint {
  const trimmed = input.trim();
  if (!/^\d+(\.\d{1,9})?$/.test(trimmed)) {
    throw new Error(
      `invalid ANIMICA amount: ${input} (must be a positive decimal with ≤ 9 fractional digits)`,
    );
  }
  const [intPart, fracPart = ""] = trimmed.split(".");
  const padded = (fracPart + "000000000").slice(0, 9);
  return BigInt(intPart + padded);
}

export function baseUnitsToAnim(units: bigint): string {
  const s = units.toString();
  if (s.length <= 9) {
    const padded = s.padStart(9, "0");
    return ("0." + padded).replace(/0+$/, "").replace(/\.$/, "");
  }
  const intPart = s.slice(0, -9);
  const fracPart = s.slice(-9).replace(/0+$/, "");
  return fracPart ? `${intPart}.${fracPart}` : intPart;
}
