import { NANM_PER_ANM } from './config';

// Formatting + parsing between nANM (integer base unit) and human ANM.
// NEVER use floats for money math — parse decimal strings exactly.

export function anmToNanm(anm: string | number): bigint {
  const s = String(anm).trim();
  if (!/^\d+(\.\d+)?$/.test(s)) throw new Error(`invalid ANM amount: ${anm}`);
  const [whole, frac = ''] = s.split('.');
  const fracPadded = (frac + '000000000').slice(0, 9);
  return BigInt(whole) * NANM_PER_ANM + BigInt(fracPadded || '0');
}

export function nanmToAnm(nanm: bigint): string {
  const neg = nanm < 0n;
  const abs = neg ? -nanm : nanm;
  const whole = abs / NANM_PER_ANM;
  const frac = abs % NANM_PER_ANM;
  const fracStr = frac.toString().padStart(9, '0').replace(/0+$/, '');
  const body = fracStr ? `${whole}.${fracStr}` : `${whole}`;
  return neg ? `-${body}` : body;
}

// Display with thousands separators + up to 4 decimals for UI.
export function formatAnm(nanm: bigint): string {
  const s = nanmToAnm(nanm);
  const [whole, frac] = s.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const shortFrac = frac ? frac.slice(0, 4) : '';
  return shortFrac ? `${grouped}.${shortFrac}` : grouped;
}

export function bps(amount: bigint, basisPoints: number): bigint {
  return (amount * BigInt(basisPoints)) / 10_000n;
}

// JSON can't serialize bigint — helper for API responses.
export function jsonSafe<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj, (_k, v) => (typeof v === 'bigint' ? v.toString() : v)));
}
