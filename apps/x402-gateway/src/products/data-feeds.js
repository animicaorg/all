'use strict';
/**
 * DATA FEEDS: a signed ANM price attestation, a holder snapshot, and a
 * mempool/network telemetry read.
 *
 * These are the reads that are cheap for us (one node call) and awkward for
 * an agent to get anywhere else, which is the honest definition of something
 * worth selling. The free public APIs are unaffected.
 *
 * ON "SIGNED". An HMAC signature would be verifiable only by us, which makes
 * it worthless to a buyer. The price attestation is therefore signed
 * secp256k1 over keccak(canonical JSON), Ethereum-style, so ANY standard
 * tooling can recover the signer address and check it against the address we
 * publish. If no signing key is configured the response says signed:false and
 * carries no signature — it never claims an attestation it cannot make. That
 * is the same rule the randomness product follows with attested:false.
 */

const crypto = require('node:crypto');
const evm = require('../facilitator-evm/evm');
const { ProductError, ProductUnavailable } = require('./errors');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** Canonical JSON: sorted keys, no whitespace. The bytes actually signed. */
function canonicalJson(obj) {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(canonicalJson).join(',') + ']';
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
}

/** hex balance | decimal string -> BigInt. Never a float. */
function toBigInt(v) {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'string') {
    const s = v.trim();
    if (/^0x[0-9a-fA-F]+$/.test(s)) return BigInt(s);
    if (/^\d+$/.test(s)) return BigInt(s);
  }
  if (typeof v === 'number' && Number.isSafeInteger(v)) return BigInt(v);
  throw new Error(`unparseable amount ${JSON.stringify(v)}`);
}

function nanmToAnm(n) {
  const v = BigInt(n);
  return `${v / 1000000000n}.${(v % 1000000000n).toString().padStart(9, '0')}`;
}

// ---------------------------------------------------------------------------
// price oracle
// ---------------------------------------------------------------------------

function createOracleProduct({ cfg, anmPrice, node, now = Date.now }) {
  // A signing key is OPTIONAL. Without one we still sell the price, but the
  // response says plainly that it is unsigned rather than implying otherwise.
  const rawKey = String(cfg.oraclePrivateKey || '').replace(/^0x/, '');
  const canSign = /^[0-9a-fA-F]{64}$/.test(rawKey);
  const signerAddress = canSign ? evm.privateKeyToAddress('0x' + rawKey) : null;

  return {
    id: 'price_oracle',
    title: 'Signed ANM price attestation',
    description:
      'The ANM/USD reference price this operator actually uses, attested at a specific chain head. Quoted from the NonKYC BID (the conservative side), with the observation timestamp and feed age included so you can judge freshness yourself.' +
      (canSign
        ? ' Each attestation is signed secp256k1 over keccak(canonical JSON) in the Ethereum style: recover the signer with any standard tooling and check it against the published signer address — you do not have to trust this response to verify it.'
        : ' NOTE: no signing key is configured on this gateway, so attestations are returned UNSIGNED (signed:false). The price is still the live one we use; it simply carries no cryptographic attestation.') +
      ' A stale or indicative feed makes this product unavailable rather than quoting a dead rate.',
    path: '/x402/oracle/price',
    routes: [{ method: 'GET', path: '/x402/oracle/price' }, { method: 'POST', path: '/x402/oracle/price' }],
    priceUsd: cfg.oraclePriceUsd,
    enabled: cfg.oracleEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 4096,
    outputSchema: {
      input: { type: 'http', method: 'GET', queryParams: {} },
      output: {
        type: 'json',
        description:
          'attestation {symbol, usd_per_anm, side, source, observed_at, age_seconds, head_height, head_hash, issued_at, nonce}, signed, signature, signer_address, message_hash, canonical_message, verification instructions',
      },
    },

    async availability() {
      const q = anmPrice.get();
      if (!q.ok) return { available: false, reason: q.reason, detail: q.detail };
      return { available: true };
    },

    async handler(ctx) {
      const q = anmPrice.get();
      if (!q.ok) throw new ProductUnavailable(q.reason, q.detail);

      let head = { height: null, hash: null };
      try {
        const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
        if (h && Number.isInteger(h.height)) head = { height: h.height, hash: h.hash || null };
      } catch {
        // The price is the product; the head is context. Missing head is
        // reported as null rather than failing the call.
      }

      const attestation = {
        age_seconds: q.age_seconds,
        head_hash: head.hash,
        head_height: head.height,
        issued_at: new Date(now()).toISOString(),
        // A nonce makes each attestation distinct, so one cannot be replayed
        // as if it were a fresh observation.
        nonce: crypto.randomBytes(16).toString('hex'),
        observed_at: q.observed_at,
        side: 'bid',
        source: q.source,
        symbol: q.symbol,
        usd_per_anm: String(q.usd_per_anm),
      };
      const message = canonicalJson(attestation);
      const digest = evm.keccak(Buffer.from(message, 'utf8'));
      const digestHex = typeof digest === 'string' ? digest : ('0x' + Buffer.from(digest).toString('hex'));

      let signature = null;
      if (canSign) {
        try {
          signature = evm.signDigest(digestHex, '0x' + rawKey);
        } catch (e) {
          signature = null;
        }
      }

      return {
        status: 200,
        bodyObj: {
          product: 'price_oracle',
          attestation,
          signed: Boolean(signature),
          signature: signature || undefined,
          signer_address: signerAddress || undefined,
          message_hash: digestHex,
          canonical_message: message,
          market_url: q.market_url,
          verification: signature
            ? {
              method: 'secp256k1 over keccak256(canonical_message)',
              rules: [
                'canonical_message is the attestation object with keys sorted and no whitespace',
                'message_hash == keccak256(utf8(canonical_message))',
                'ecrecover(message_hash, signature) == signer_address',
              ],
              note: 'Verifiable with any Ethereum tooling. You do not have to trust this response to check it.',
            }
            : {
              method: null,
              note: 'UNSIGNED: no oracle signing key is configured on this gateway (X402_ORACLE_PRIVATE_KEY). The price is the live one we use, but it carries no attestation you can verify independently.',
            },
          disclosure:
            'Quoted from the NonKYC bid — the conservative side of the book — not last or mid. This is one exchange, and ANM is thinly traded; treat it as this operator\'s reference rate rather than a global price.',
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// holder snapshot
// ---------------------------------------------------------------------------

function createSnapshotProduct({ cfg, node, now = Date.now }) {
  return {
    id: 'holder_snapshot',
    title: 'Holder snapshot / rich list',
    description:
      `A ranked snapshot of ANM holders at a pinned chain height — up to ${cfg.snapshotMaxHolders} accounts with exact nANM balances as decimal strings, plus the total address count and the head it was taken at. The shape airdrops, governance weighting and distribution analysis actually need, in one call instead of thousands of balance lookups.`,
    path: '/x402/chain/holders',
    routes: [{ method: 'POST', path: '/x402/chain/holders' }, { method: 'GET', path: '/x402/chain/holders' }],
    priceUsd: cfg.snapshotPriceUsd,
    enabled: cfg.snapshotEnabled,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 8192,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          limit: { type: 'integer', required: false, description: `how many holders to return, 1..${cfg.snapshotMaxHolders} (default 100)` },
        },
        queryParams: { limit: { type: 'integer', description: 'same as body.limit' } },
      },
      output: {
        type: 'json',
        description: 'as_of {height}, total_addresses, count, holders[] {rank, address, balance_nanm, balance_anm, share_percent}, total_supply_observed',
      },
    },

    async availability() {
      try {
        const r = await node.call('state.getRichList', { limit: 1 }, { timeoutMs: 6000 });
        if (!r || !Array.isArray(r.items)) return { available: false, reason: 'richlist_unavailable', detail: 'node returned no items' };
        return { available: true };
      } catch (e) {
        return { available: false, reason: 'node_unreachable', detail: e.message };
      }
    },

    validate(ctx) {
      const b = ctx.json || {};
      const raw = b.limit !== undefined ? b.limit : ctx.query.get('limit');
      let limit = 100;
      if (raw !== undefined && raw !== null && raw !== '') {
        const n = Number(raw);
        if (!Number.isInteger(n) || n < 1) throw bad('limit must be a positive integer', 'invalid_request');
        if (n > Number(cfg.snapshotMaxHolders)) {
          throw bad(`limit ${n} exceeds the cap of ${cfg.snapshotMaxHolders}`, 'limit_too_high', {
            caps: { max_holders: Number(cfg.snapshotMaxHolders) },
          });
        }
        limit = n;
      }
      return { limit };
    },

    async preSettle() {
      const r = await node.call('state.getRichList', { limit: 1 }, { timeoutMs: 6000 });
      if (!r || !Array.isArray(r.items)) throw new ProductUnavailable('richlist_unavailable', 'node returned no rich list');
      return { height: r.height };
    },

    async handler(ctx) {
      const { limit } = ctx.params;
      let r;
      try {
        r = await node.call('state.getRichList', { limit }, { timeoutMs: 20000 });
      } catch (e) {
        const err = new Error(`rich list failed: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      const items = Array.isArray(r.items) ? r.items : [];

      let total = 0n;
      const holders = items.map((it) => {
        let bal;
        try {
          bal = toBigInt(it.balance);
        } catch {
          bal = null;
        }
        if (bal !== null) total += bal;
        return {
          rank: it.rank,
          address: it.address,
          balance_nanm: bal === null ? null : bal.toString(),
          balance_anm: bal === null ? null : nanmToAnm(bal),
        };
      });

      // Share is of the SUM OF THE RETURNED ROWS, not of total supply — say
      // which, because quoting a percentage against the wrong denominator is
      // how a snapshot silently misleads.
      for (const h of holders) {
        h.share_of_returned_percent = h.balance_nanm && total > 0n
          ? Number((BigInt(h.balance_nanm) * 1000000n) / total) / 10000
          : null;
      }

      let totalSupply = null;
      try {
        const s = await node.call('state.getTotalSupply', {}, { timeoutMs: 6000 });
        totalSupply = toBigInt(typeof s === 'object' && s !== null ? (s.total_supply || s.totalSupply || s.value) : s).toString();
      } catch {
        totalSupply = null;
      }

      return {
        status: 200,
        bodyObj: {
          product: 'holder_snapshot',
          as_of: { height: r.height !== undefined ? r.height : ctx.pinned.height, taken_at: new Date(now()).toISOString() },
          total_addresses: r.totalAddresses !== undefined ? r.totalAddresses : null,
          count: holders.length,
          requested_limit: limit,
          sum_of_returned_nanm: total.toString(),
          sum_of_returned_anm: nanmToAnm(total),
          total_supply_nanm: totalSupply,
          holders,
          notes: {
            units: 'balances are nANM (1e-9 ANM) as exact decimal strings — divide by 1e9 with a decimal library, never a float',
            share: 'share_of_returned_percent is a share of the rows RETURNED, not of total supply; use total_supply_nanm if you want the latter',
            addresses: 'addresses are 32-byte account digests as returned by the node',
          },
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// mempool / network telemetry
// ---------------------------------------------------------------------------

function createMempoolProduct({ cfg, node, now = Date.now }) {
  return {
    id: 'mempool_feed',
    title: 'Mempool and network telemetry',
    description:
      'A point-in-time read of the Animica mempool and network: pending transaction count and ids, total pending bytes, the age of the oldest entry, chain head, peer count and network hashrate. What a bot watching for congestion, fee conditions or inclusion timing needs, without running a node.',
    path: '/x402/chain/mempool',
    routes: [{ method: 'GET', path: '/x402/chain/mempool' }, { method: 'POST', path: '/x402/chain/mempool' }],
    priceUsd: cfg.mempoolPriceUsd,
    enabled: cfg.mempoolEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 4096,
    outputSchema: {
      input: { type: 'http', method: 'GET', queryParams: {} },
      output: {
        type: 'json',
        description: 'mempool {count, total_bytes, oldest_age_seconds, pending_txids[]}, head {height, hash}, network {peers, hashrate}, observed_at',
      },
    },

    async availability() {
      try {
        await node.call('mempool.getStats', {}, { timeoutMs: 5000 });
        return { available: true };
      } catch (e) {
        return { available: false, reason: 'node_unreachable', detail: e.message };
      }
    },

    async handler(ctx) {
      // One batch, and a failure of any SINGLE feed is data (null) rather
      // than a poisoned response — the caller paid for a telemetry read and
      // should get whatever the node could answer, clearly labelled.
      const calls = [
        { method: 'mempool.getStats', params: {} },
        { method: 'mempool.getPending', params: {} },
        { method: 'chain.getHead', params: {} },
        { method: 'net.peerCount', params: {} },
        { method: 'chain.getNetworkHashrate', params: {} },
      ];
      let results;
      try {
        results = await node.batchSettled(calls, { timeoutMs: 15000 });
      } catch (e) {
        const err = new Error(`telemetry batch failed: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      const pick = (i) => (results[i] && results[i].ok ? results[i].result : null);
      const errOf = (i) => (results[i] && !results[i].ok ? String(results[i].error || 'failed') : undefined);

      const stats = pick(0) || {};
      const pending = pick(1);
      const head = pick(2) || {};
      const peers = pick(3);
      const hashrate = pick(4);

      const txids = Array.isArray(pending) ? pending : (pending && Array.isArray(pending.txids) ? pending.txids : []);

      return {
        status: 200,
        bodyObj: {
          product: 'mempool_feed',
          observed_at: new Date(now()).toISOString(),
          mempool: {
            count: stats.count !== undefined ? stats.count : null,
            total_bytes: stats.totalBytes !== undefined ? stats.totalBytes : null,
            oldest_age_seconds: stats.oldestAgeSec !== undefined ? stats.oldestAgeSec : null,
            pending_txids: txids,
            pending_returned: txids.length,
            error: errOf(0) || errOf(1),
          },
          head: {
            height: head.height !== undefined ? head.height : null,
            hash: head.hash || null,
            canonical_height: head.canonicalHeight !== undefined ? head.canonicalHeight : null,
            error: errOf(2),
          },
          network: {
            peers: typeof peers === 'number' ? peers : (peers && peers.count !== undefined ? peers.count : null),
            hashrate: hashrate && typeof hashrate === 'object' ? (hashrate.hashrate ?? hashrate.value ?? null) : hashrate,
            error: errOf(3) || errOf(4),
          },
          note:
            'A point-in-time read from ONE node. The mempool is per-node state, not consensus: another node may hold a different set, and an absent transaction here does not prove it was never broadcast.',
        },
      };
    },
  };
}

module.exports = {
  createOracleProduct, createSnapshotProduct, createMempoolProduct,
  canonicalJson, toBigInt, nanmToAnm,
};
