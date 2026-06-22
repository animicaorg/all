/**
 * ena_pay.js — on-chain ANM deposit verification for the metered ENA budget.
 *
 * A user funds their prepaid ENA budget by sending ANM to the studio treasury
 * with their wallet (a single signature). This module verifies that deposit
 * against the Animica node RPC BEFORE we credit the budget — we NEVER trust a
 * client-supplied amount; the value and recipient are read from the chain.
 *
 * Verification mirrors the Python prior art in
 *   /root/animica/python/animica/ena/payments.py (verify_payment)
 *   /root/animica/rpc/methods/aicf_jobs.py
 * which binds a payment by recipient + amount + txid (no memo; the live v2 tx
 * wire format has no memo field — see MEMORY: reference_node_tx_no_memo).
 *
 * Node RPC (http://127.0.0.1:8545/rpc):
 *   tx.getTransactionByHash(txHash) -> {hash, from, to, value, blockNumber, ...}
 *     - `to`    : the 32-byte recipient digest as hex (no 0x). For an anim1…
 *                 bech32m address, that digest is the address payload minus the
 *                 2-byte alg_id prefix (payload = alg_id(2) || sha3(pubkey)(32)).
 *     - `value` : integer ANM base units (nano; 9 decimals).
 *     - a persisted tx carries `blockNumber`; a pending one does not.
 *
 * No third-party deps: a minimal bech32m decoder is vendored below so we can
 * derive the treasury digest from its anim1… address with zero install cost.
 */

const RPC_URL = process.env.STUDIO_NODE_RPC || 'http://127.0.0.1:8545/rpc';
const ANM_DECIMALS = 9;
const NANO = 10 ** ANM_DECIMALS;

// Status strings (from the node) that mean the tx is on-chain. We also accept a
// present blockNumber as proof of inclusion (getTransactionByHash omits status
// but sets blockNumber for persisted txs).
const CONFIRMED = new Set(['confirmed', 'included', 'mined', 'finalized', 'success', 'ok', 'accepted']);
const FAILED = new Set(['rejected', 'failed', 'invalid', 'dropped', 'expired']);

// --------------------------------------------------------------------------- //
// bech32m decode (vendored, zero-dep) — BIP-0350 constant.
// --------------------------------------------------------------------------- //
const BECH32M_CONST = 0x2bc830a3;
const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';

function bech32Polymod(values) {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) chk ^= ((top >>> i) & 1) ? GEN[i] : 0;
  }
  return chk >>> 0;
}

function hrpExpand(hrp) {
  const out = [];
  for (let i = 0; i < hrp.length; i++) out.push(hrp.charCodeAt(i) >>> 5);
  out.push(0);
  for (let i = 0; i < hrp.length; i++) out.push(hrp.charCodeAt(i) & 31);
  return out;
}

/** Decode a bech32m string -> {hrp, data:number[]} (5-bit groups), or null. */
function bech32mDecode(str) {
  if (typeof str !== 'string' || str.length < 8 || str.length > 200) return null;
  const lower = str.toLowerCase();
  if (str !== lower && str !== str.toUpperCase()) return null; // mixed case
  const s = lower;
  const pos = s.lastIndexOf('1');
  if (pos < 1 || pos + 7 > s.length) return null;
  const hrp = s.slice(0, pos);
  const dataPart = s.slice(pos + 1);
  const data = [];
  for (const ch of dataPart) {
    const d = CHARSET.indexOf(ch);
    if (d === -1) return null;
    data.push(d);
  }
  const chk = bech32Polymod(hrpExpand(hrp).concat(data));
  if (chk !== BECH32M_CONST) return null;
  return { hrp, data: data.slice(0, data.length - 6) };
}

/** convertbits 5->8 (no padding tolerated in trailing bits). */
function convertbits5to8(data) {
  let acc = 0;
  let bits = 0;
  const out = [];
  const maxv = 255;
  for (const value of data) {
    if (value < 0 || value >> 5 !== 0) return null;
    acc = ((acc << 5) | value) & 0xffffff;
    bits += 5;
    while (bits >= 8) {
      bits -= 8;
      out.push((acc >>> bits) & maxv);
    }
  }
  if (bits >= 5 || ((acc << (8 - bits)) & maxv)) return null;
  return out;
}

/**
 * Decode an anim1… address to its 32-byte recipient digest as lowercase hex
 * (no 0x). payload = alg_id(2 bytes BE) || sha3_256(pubkey)(32 bytes); the chain
 * stores the 32-byte digest as the tx `to`. Throws on a malformed address.
 */
export function addressToDigestHex(addr) {
  const dec = bech32mDecode(addr);
  if (!dec || dec.hrp !== 'anim') {
    throw Object.assign(new Error(`invalid treasury address ${addr}`), { code: 'BAD_ADDRESS' });
  }
  const bytes = convertbits5to8(dec.data);
  if (!bytes || bytes.length !== 34) {
    throw Object.assign(new Error(`bad address payload length for ${addr}`), { code: 'BAD_ADDRESS' });
  }
  const digest = bytes.slice(2); // drop the 2-byte alg_id prefix
  return Buffer.from(digest).toString('hex');
}

function normHex(h) {
  const s = String(h == null ? '' : h).toLowerCase();
  return s.startsWith('0x') ? s.slice(2) : s;
}

export function anmToNano(anm) {
  return Math.round(Number(anm) * NANO);
}
export function nanoToAnm(nano) {
  return Number(nano) / NANO;
}

// --------------------------------------------------------------------------- //
// RPC
// --------------------------------------------------------------------------- //
let _id = 0;
async function rpcCall(method, params, { fetchImpl = fetch } = {}) {
  _id += 1;
  const r = await fetchImpl(RPC_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: _id, method, params }),
  });
  if (!r.ok) throw Object.assign(new Error(`RPC ${method} HTTP ${r.status}`), { code: 'RPC' });
  const payload = await r.json();
  if (payload && payload.error) {
    throw Object.assign(new Error(`RPC ${method} error: ${JSON.stringify(payload.error)}`), { code: 'RPC' });
  }
  return payload ? payload.result : null;
}

// --------------------------------------------------------------------------- //
// verifyDeposit
// --------------------------------------------------------------------------- //
/**
 * Verify an ANM deposit funds a budget top-up.
 *
 * @param {object}  args
 * @param {string}  args.txid           the on-chain transaction hash
 * @param {string}  args.expectedTo     the treasury address (anim1…) the funds must go to
 * @param {number}  args.minAmountAnm   minimum acceptable amount (ANM decimal) — e.g. the per-call price
 * @param {Function}[args.fetchImpl]    injectable fetch (for tests)
 * @returns {Promise<{ok:boolean, amountAnm:number, reason:string, pending:boolean, from:string|null}>}
 *
 * `pending:true` means the tx exists but isn't on-chain yet (caller may retry);
 * `ok:false, pending:false` is a hard rejection (unknown / wrong recipient /
 * underpaid / failed). The amount is ALWAYS read from the chain, never the client.
 */
export async function verifyDeposit({ txid, expectedTo, minAmountAnm = 0, fetchImpl = fetch } = {}) {
  const out = { ok: false, amountAnm: 0, reason: '', pending: false, from: null, txid: normHex(txid) };

  if (!txid || typeof txid !== 'string') { out.reason = 'bad_txid'; return out; }

  // Decode the treasury digest first — a config error must not look like a user error.
  let wantHex;
  try {
    wantHex = addressToDigestHex(expectedTo);
  } catch (e) {
    out.reason = 'bad_treasury';
    throw Object.assign(new Error(out.reason), { cause: e });
  }

  let tx;
  try {
    tx = await rpcCall('tx.getTransactionByHash', [txid], { fetchImpl });
  } catch (e) {
    // RPC unreachable / errored — treat as pending so the client can retry,
    // but never credit.
    out.reason = 'rpc_error';
    out.pending = true;
    return out;
  }

  if (!tx) {
    out.reason = 'tx_not_found';
    out.pending = true; // may simply not have propagated yet
    return out;
  }

  const toHex = normHex(tx.to != null ? tx.to : tx.to_addr);
  if (toHex !== wantHex) { out.reason = 'wrong_recipient'; return out; }

  let valueNano;
  try {
    valueNano = Number(tx.value);
    if (!Number.isFinite(valueNano)) throw new Error('NaN');
  } catch {
    out.reason = 'bad_value';
    return out;
  }
  out.amountAnm = nanoToAnm(valueNano);
  out.from = tx.from != null ? tx.from : (tx.from_addr != null ? tx.from_addr : null);

  // Confirmation: a present blockNumber proves inclusion; otherwise trust an
  // explicit confirmed status. Anything else is still pending.
  const status = String(tx.status || '').toLowerCase();
  const hasBlock = tx.blockNumber != null && tx.blockNumber !== '';
  if (status && FAILED.has(status)) { out.reason = `tx_${status}`; return out; }
  if (!hasBlock && !(status && CONFIRMED.has(status))) {
    out.reason = 'awaiting_confirmation';
    out.pending = true;
    return out;
  }

  // Amount check AFTER confirmation so a confirmed-but-underpaid tx is a hard
  // reject (not a retry).
  if (valueNano < anmToNano(minAmountAnm)) {
    out.reason = 'underpaid';
    return out;
  }

  out.ok = true;
  out.reason = 'verified';
  return out;
}

export default { verifyDeposit, addressToDigestHex, anmToNano, nanoToAnm };
