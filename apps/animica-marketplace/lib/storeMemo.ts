import { randomBytes } from 'node:crypto';
import { isAnimicaAddress } from './address';
import { NANM_PER_ANM } from './config';

// ANMSTORE1 purchase memo: the exact tx.data bytes a buyer's wallet attaches to its kind=0
// transfer to the store treasury. Modeled byte-for-byte on the ANMSETL1 anchor discipline
// (consensus/iou_settlement.py parse_anchor_payload / encode_anchor_payload):
//   - exact magic prefix, strict compact JSON, EXACT key set {v,pid,b,l,a,n};
//   - ANY anomaly (unknown key, wrong type, bad charset, non-canonical encoding, oversize,
//     float/exponent/leading-zero amounts) rejects the WHOLE memo — fail-closed, never
//     partially interpreted, no guessing;
//   - the builder strict-re-parses its own output before returning, so we can never emit
//     bytes our own parser would reject.
// The amount `a` is extracted from the raw JSON text as a BigInt (never through the JS
// number path) so amounts above 2^53 nANM stay exact.

export const STORE_MEMO_MAGIC = 'ANMSTORE1';
export const STORE_MEMO_MAX_BYTES = 300; // well under the 1 KiB transfer-data mempool cap

// Chain-wide money cap (consensus/rewards.py MAX_MONEY = 900,000,000 ANM) in nANM.
export const MAX_MEMO_AMOUNT_NANM = 900_000_000n * NANM_PER_ANM;

const MEMO_KEYS = ['v', 'pid', 'b', 'l', 'a', 'n'] as const;
const ID_RE = /^[A-Za-z0-9_-]{10,64}$/; // cuid-shaped row ids
const NONCE_RE = /^[0-9a-f]{16}$/;

export interface StoreMemo {
  v: 1;
  pid: string; // purchaseId
  b: string; // buyer anim1... address
  l: string; // listingId
  a: bigint; // amountNanm — exact integer, never a JS float
  n: string; // 16-hex single-use nonce
}

export type StoreMemoParse =
  | { ok: true; memo: StoreMemo }
  | { ok: false; reason: string };

// Fresh 16-hex nonce for a payment intent (single-use, baked into the memo bytes).
export function newStoreNonce(): string {
  return randomBytes(8).toString('hex');
}

function canonicalJson(m: { pid: string; b: string; l: string; a: bigint; n: string }): string {
  // Field charsets are validated to exclude quotes/backslashes, so no JSON escaping can occur
  // and this hand-built compact encoding IS the canonical JSON.stringify output.
  return `{"v":1,"pid":"${m.pid}","b":"${m.b}","l":"${m.l}","a":${m.a},"n":"${m.n}"}`;
}

function validateFields(m: { pid: string; b: string; l: string; a: bigint; n: string }): string | null {
  if (typeof m.pid !== 'string' || !ID_RE.test(m.pid)) return 'bad pid';
  if (typeof m.b !== 'string' || !isAnimicaAddress(m.b)) return 'bad buyer address';
  if (typeof m.l !== 'string' || !ID_RE.test(m.l)) return 'bad listing id';
  if (typeof m.a !== 'bigint' || m.a <= 0n || m.a > MAX_MEMO_AMOUNT_NANM) return 'bad amount';
  if (typeof m.n !== 'string' || !NONCE_RE.test(m.n)) return 'bad nonce';
  return null;
}

// Build the exact memo bytes for a payment intent. Throws on invalid input — the server must
// never mint an intent whose memo its own watcher would reject (ANMSETL1 encode discipline).
export function buildStoreMemo(m: { pid: string; b: string; l: string; a: bigint; n: string }): Uint8Array {
  const bad = validateFields(m);
  if (bad) throw new Error(`storeMemo build: ${bad}`);
  const bytes = Buffer.from(STORE_MEMO_MAGIC + canonicalJson(m), 'utf8');
  if (bytes.length > STORE_MEMO_MAX_BYTES) throw new Error(`storeMemo build: memo ${bytes.length} bytes exceeds ${STORE_MEMO_MAX_BYTES}`);
  const reparsed = parseStoreMemo(bytes);
  if (!reparsed.ok) throw new Error(`storeMemo build: encoded memo failed strict re-parse: ${reparsed.reason}`);
  return new Uint8Array(bytes);
}

// Same, as lowercase hex (no 0x) — the form stored in StorePaymentIntent.memoHex and
// compared byte-for-byte against the on-chain tx data by the payment watcher.
export function buildStoreMemoHex(m: { pid: string; b: string; l: string; a: bigint; n: string }): string {
  return Buffer.from(buildStoreMemo(m)).toString('hex');
}

// Strict fail-closed parse of candidate tx data bytes. Returns { ok: false } on ANY anomaly;
// the caller must treat that as "not a store payment", never as a partial match.
export function parseStoreMemo(data: Uint8Array): StoreMemoParse {
  if (!(data instanceof Uint8Array)) return { ok: false, reason: 'not bytes' };
  if (data.length > STORE_MEMO_MAX_BYTES) return { ok: false, reason: 'oversize' };
  if (data.length <= STORE_MEMO_MAGIC.length) return { ok: false, reason: 'too short' };

  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    return { ok: false, reason: 'not utf-8' };
  }
  if (!text.startsWith(STORE_MEMO_MAGIC)) return { ok: false, reason: 'bad magic' };
  const jsonText = text.slice(STORE_MEMO_MAGIC.length);

  let obj: any;
  try {
    obj = JSON.parse(jsonText);
  } catch {
    return { ok: false, reason: 'bad json' };
  }
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) return { ok: false, reason: 'not an object' };
  const keys = Object.keys(obj);
  if (keys.length !== MEMO_KEYS.length || MEMO_KEYS.some((k) => !(k in obj))) {
    return { ok: false, reason: 'wrong key set' };
  }
  if (typeof obj.v !== 'number' || obj.v !== 1) return { ok: false, reason: 'bad version' };
  if (typeof obj.a !== 'number') return { ok: false, reason: 'amount not a json number' };
  if (typeof obj.pid !== 'string' || !ID_RE.test(obj.pid)) return { ok: false, reason: 'bad pid' };
  if (typeof obj.b !== 'string' || !isAnimicaAddress(obj.b)) return { ok: false, reason: 'bad buyer address' };
  if (typeof obj.l !== 'string' || !ID_RE.test(obj.l)) return { ok: false, reason: 'bad listing id' };
  if (typeof obj.n !== 'string' || !NONCE_RE.test(obj.n)) return { ok: false, reason: 'bad nonce' };

  // Exact-integer amount: pull the digits straight out of the JSON text (JSON.parse would
  // round anything above 2^53). All string fields are quote-free by the charset checks above,
  // so the only `"a":` occurrence is the real key.
  const aMatches = [...jsonText.matchAll(/"a":(0|[1-9][0-9]*)/g)];
  if (aMatches.length !== 1) return { ok: false, reason: 'bad amount encoding' };
  const digits = aMatches[0][1];
  if (digits.length > 19) return { ok: false, reason: 'amount too large' };
  const a = BigInt(digits);
  if (a <= 0n || a > MAX_MEMO_AMOUNT_NANM) return { ok: false, reason: 'amount out of range' };

  // Canonical-encoding check: re-encode from the parsed fields and require byte equality.
  // Kills whitespace/key-order/number-format variants in one stroke (no memo malleability —
  // there is exactly ONE byte string per logical memo).
  if (text !== STORE_MEMO_MAGIC + canonicalJson({ pid: obj.pid, b: obj.b, l: obj.l, a, n: obj.n })) {
    return { ok: false, reason: 'non-canonical encoding' };
  }

  return { ok: true, memo: { v: 1, pid: obj.pid, b: obj.b, l: obj.l, a, n: obj.n } };
}

// Parse from a hex string (accepts optional 0x prefix; strict hex otherwise).
export function parseStoreMemoHex(hex: string): StoreMemoParse {
  if (typeof hex !== 'string') return { ok: false, reason: 'not a string' };
  const h = (hex.startsWith('0x') || hex.startsWith('0X') ? hex.slice(2) : hex).toLowerCase();
  if (h.length % 2 !== 0 || !/^[0-9a-f]*$/.test(h)) return { ok: false, reason: 'bad hex' };
  return parseStoreMemo(new Uint8Array(Buffer.from(h, 'hex')));
}
