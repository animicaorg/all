/*
 * Animica Quantum Randomness Beacon — in-browser verifier.
 *
 * Reimplements, byte-for-byte, the server's randomness math so anyone can verify
 * beacon rounds and draws WITHOUT trusting the server:
 *   - sha3_256 (FIPS 202)
 *   - the deterministic DRNG (randomness/qrng/public.py:QDRNG)
 *   - the draw primitives (lottery/dice/coin/range/choice/weighted/shuffle/bytes)
 *   - beacon-integrity: value == sha3_256(BEACON_DOMAIN||round_be8||prev||agg)
 *
 * Works in the browser (window.AnimicaBeacon) and in Node (module.exports).
 * No dependencies.
 */
(function (root) {
  "use strict";
  const MASK = (1n << 64n) - 1n;
  const RC = [
    0x0000000000000001n,0x0000000000008082n,0x800000000000808An,0x8000000080008000n,
    0x000000000000808Bn,0x0000000080000001n,0x8000000080008081n,0x8000000000008009n,
    0x000000000000008An,0x0000000000000088n,0x0000000080008009n,0x000000008000000An,
    0x000000008000808Bn,0x800000000000008Bn,0x8000000000008089n,0x8000000000008003n,
    0x8000000000008002n,0x8000000000000080n,0x000000000000800An,0x800000008000000An,
    0x8000000080008081n,0x8000000000008080n,0x0000000080000001n,0x8000000080008008n,
  ];
  const ROT = [0,1,62,28,27, 36,44,6,55,20, 3,10,43,25,39, 41,45,15,21,8, 18,2,61,56,14];

  function rotl(x, n) { n = BigInt(n); return ((x << n) | (x >> (64n - n))) & MASK; }

  function keccakF(A) {
    for (let round = 0; round < 24; round++) {
      const C = new Array(5);
      for (let x = 0; x < 5; x++) C[x] = A[x] ^ A[x + 5] ^ A[x + 10] ^ A[x + 15] ^ A[x + 20];
      const D = new Array(5);
      for (let x = 0; x < 5; x++) D[x] = C[(x + 4) % 5] ^ rotl(C[(x + 1) % 5], 1n);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 25; y += 5) A[x + y] = (A[x + y] ^ D[x]) & MASK;
      const B = new Array(25).fill(0n);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++)
        B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl(A[x + 5 * y], ROT[x + 5 * y]);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++)
        A[x + 5 * y] = (B[x + 5 * y] ^ ((~B[((x + 1) % 5) + 5 * y] & MASK) & B[((x + 2) % 5) + 5 * y])) & MASK;
      A[0] = (A[0] ^ RC[round]) & MASK;
    }
  }

  function sha3_256(msg) {
    const RATE = 136;
    const padLen = RATE - (msg.length % RATE);
    const m = new Uint8Array(msg.length + padLen);
    m.set(msg);
    m[msg.length] ^= 0x06;
    m[m.length - 1] ^= 0x80;
    const A = new Array(25).fill(0n);
    for (let off = 0; off < m.length; off += RATE) {
      for (let i = 0; i < RATE / 8; i++) {
        let lane = 0n;
        for (let b = 0; b < 8; b++) lane |= BigInt(m[off + i * 8 + b]) << BigInt(8 * b);
        A[i] ^= lane;
      }
      keccakF(A);
    }
    const out = new Uint8Array(32);
    for (let i = 0; i < 4; i++) {
      let lane = A[i];
      for (let b = 0; b < 8; b++) { out[i * 8 + b] = Number(lane & 0xffn); lane >>= 8n; }
    }
    return out;
  }

  // --- byte helpers ---
  const enc = (typeof TextEncoder !== "undefined") ? new TextEncoder() : null;
  function strBytes(s) {
    if (enc) return enc.encode(s);
    return Uint8Array.from(Buffer.from(s, "utf8"));
  }
  function hexToBytes(h) {
    if (!h) return new Uint8Array(0);
    if (h.startsWith("0x")) h = h.slice(2);
    const out = new Uint8Array(h.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(i * 2, 2), 16);
    return out;
  }
  function bytesToHex(b) {
    let s = "";
    for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, "0");
    return s;
  }
  function concat() {
    let n = 0; for (const a of arguments) n += a.length;
    const out = new Uint8Array(n); let o = 0;
    for (const a of arguments) { out.set(a, o); o += a.length; }
    return out;
  }
  function be8(num) {
    const out = new Uint8Array(8); let v = num;
    for (let i = 7; i >= 0; i--) { out[i] = v & 0xff; v = Math.floor(v / 256); }
    return out;
  }
  function bitLength(n) { let b = 0, v = n; while (v > 0) { b++; v = Math.floor(v / 2); } return b; }

  // --- DRNG (mirror of public.py) ---
  const PUB_DOMAIN = strBytes("animica/qrng/public/v1");
  const BEACON_DOMAIN = strBytes("animica/qrng/beacon/v1");

  function drngSeed(beacon, kind, requestId) {
    return sha3_256(concat(PUB_DOMAIN, strBytes("|k:"), strBytes(kind),
      strBytes("|r:"), strBytes(requestId), strBytes("|b:"), beacon));
  }

  class QDRNG {
    constructor(seed) { this.seed = seed; this.ctr = 0; this.buf = new Uint8Array(0); }
    _refill() { this.buf = concat(this.buf, sha3_256(concat(this.seed, be8(this.ctr)))); this.ctr++; }
    randbytes(n) {
      while (this.buf.length < n) this._refill();
      const out = this.buf.slice(0, n); this.buf = this.buf.slice(n); return out;
    }
    randbelow(n) {
      if (n <= 1) return 0;
      const nb = BigInt(n);
      const k = Math.floor((bitLength(n) + 7) / 8) + 1;
      const limit = 1n << BigInt(8 * k);
      const threshold = limit - (limit % nb);
      for (;;) {
        const xb = this.randbytes(k);
        let x = 0n; for (let i = 0; i < xb.length; i++) x = (x << 8n) | BigInt(xb[i]);
        if (x < threshold) return Number(x % nb);
      }
    }
    randrange(lo, hi) { return lo + this.randbelow(hi - lo + 1); }
    shuffle(items) {
      const a = items.slice();
      for (let i = a.length - 1; i > 0; i--) { const j = this.randbelow(i + 1); const t = a[i]; a[i] = a[j]; a[j] = t; }
      return a;
    }
    sample(items, k) {
      const a = items.slice(); const out = [];
      for (let i = 0; i < k; i++) { const j = i + this.randbelow(a.length - i); const t = a[i]; a[i] = a[j]; a[j] = t; out.push(a[i]); }
      return out;
    }
    weightedIndex(weights) {
      let total = 0; for (const w of weights) total += Number(w);
      const r = this.randbelow(total); let acc = 0;
      for (let i = 0; i < weights.length; i++) { acc += Number(weights[i]); if (r < acc) return i; }
      return weights.length - 1;
    }
  }

  function mk(kind, beacon, round, rid, params, output) {
    return { kind, beacon_hex: bytesToHex(beacon), round_id: round, request_id: rid, params, output };
  }

  function compute(kind, beacon, round, rid, p) {
    let rng;
    if (kind === "lottery") { rng = new QDRNG(drngSeed(beacon, "lottery", rid)); return mk(kind, beacon, round, rid, p, rng.sample(p.entries, p.k)); }
    if (kind === "choice") { rng = new QDRNG(drngSeed(beacon, "choice", rid)); return mk(kind, beacon, round, rid, p, p.items[rng.randbelow(p.items.length)]); }
    if (kind === "weighted") { rng = new QDRNG(drngSeed(beacon, "weighted", rid)); return mk(kind, beacon, round, rid, p, p.items[rng.weightedIndex(p.weights)]); }
    if (kind === "shuffle") { rng = new QDRNG(drngSeed(beacon, "shuffle", rid)); return mk(kind, beacon, round, rid, p, rng.shuffle(p.items)); }
    if (kind === "range") { rng = new QDRNG(drngSeed(beacon, "range", rid)); const o = []; const c = p.count == null ? 1 : p.count; for (let i = 0; i < c; i++) o.push(rng.randrange(p.lo, p.hi)); return mk(kind, beacon, round, rid, p, o); }
    if (kind === "coin") { rng = new QDRNG(drngSeed(beacon, "coin", rid)); const o = []; const c = p.count == null ? 1 : p.count; for (let i = 0; i < c; i++) o.push(rng.randbelow(2) ? "H" : "T"); return mk(kind, beacon, round, rid, p, o); }
    if (kind === "dice") { rng = new QDRNG(drngSeed(beacon, "dice", rid)); const o = []; const c = p.count == null ? 1 : p.count; for (let i = 0; i < c; i++) o.push(1 + rng.randbelow(p.sides)); return mk(kind, beacon, round, rid, p, o); }
    if (kind === "bytes") { rng = new QDRNG(drngSeed(beacon, "bytes", rid)); return mk(kind, beacon, round, rid, p, bytesToHex(rng.randbytes(p.n))); }
    throw new Error("unknown kind: " + kind);
  }

  function verifyResult(res) {
    try {
      const recomputed = compute(res.kind, hexToBytes(res.beacon_hex), res.round_id, res.request_id, res.params);
      return JSON.stringify(recomputed.output) === JSON.stringify(res.output);
    } catch (e) { return false; }
  }

  // beacon integrity: value == sha3_256(BEACON_DOMAIN || round_be8 || prev || aggregate)
  function verifyBeacon(round, prevHex, aggregateCommitmentHex, valueHex) {
    const computed = sha3_256(concat(BEACON_DOMAIN, be8(round), hexToBytes(prevHex), hexToBytes(aggregateCommitmentHex)));
    return bytesToHex(computed) === (valueHex || "").toLowerCase();
  }

  const API = { sha3_256, bytesToHex, hexToBytes, compute, verifyResult, verifyBeacon, QDRNG, drngSeed };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  if (root) root.AnimicaBeacon = API;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
