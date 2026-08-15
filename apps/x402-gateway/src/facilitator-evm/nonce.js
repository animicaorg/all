'use strict';
/**
 * The facilitator account's transaction-nonce allocator.
 *
 * WHY THIS EXISTS. Two writers sign from the same EOA: the settlement engine
 * and the treasury. Both used to derive their nonce from
 * `eth_getTransactionCount(addr, 'pending')` immediately before signing, and a
 * FIFO submit lock around that sequence is NOT enough: the lock serialises our
 * code, not the RPC front-end's view of the mempool. A load-balanced public
 * endpoint (the production X402_RPC_URL is exactly that) can answer `pending`
 * from a node that has not yet seen the transaction we broadcast a
 * millisecond ago. The next signer then re-uses that nonce, the node answers
 * `replacement transaction underpriced`, and the settlement engine treats a
 * non-transport send error as a definitive rejection: the payer's
 * authorization is burned at this facilitator for a sip's sake.
 *
 * THE RULE. One allocator per facilitator account, used by every writer
 * INSIDE the submit lock:
 *
 *     nonce = max(remote_pending, last_issued + 1)
 *
 * `last_issued` is a high-water mark, committed only once the raw transaction
 * has actually been handed to the node (or may have been — a transport error
 * is an unknown outcome and MUST hold the nonce, or a retry would collide with
 * a transaction that did land).
 *
 * THE TTL. A high-water mark that only ever moves up would strand the lane
 * forever if a transaction were dropped from every mempool: `remote` would
 * stay behind and we would keep signing into a gap. The mark is therefore only
 * honoured while it is FRESH (default 120 s — RPC propagation lag is seconds,
 * not minutes). Past that window chain truth wins again, which is also exactly
 * the right recovery: re-signing the dropped transaction's own nonce.
 *
 * Nothing here touches keys, money or storage. It is deliberately tiny: the
 * only shared mutable state between the two writers is these two numbers.
 */

const { quantityToBigInt } = require('./evm');

const DEFAULT_HIGH_WATER_TTL_MS = 120_000;

/**
 * @param {object}   opts
 * @param {object}   opts.rpc      JSON-RPC client (`call(method, params)`).
 * @param {string}   opts.address  the account whose nonce lane this is.
 * @param {function} [opts.now]    injectable clock (ms).
 * @param {number}   [opts.ttlMs]  how long a high-water mark stays authoritative.
 */
function createNonceAllocator({ rpc, address, now = Date.now, ttlMs = DEFAULT_HIGH_WATER_TTL_MS, logger = null }) {
  if (!rpc || typeof rpc.call !== 'function') throw new Error('createNonceAllocator: rpc client is required');
  if (!address) throw new Error('createNonceAllocator: address is required');

  let lastIssued = null; // highest nonce handed out AND broadcast (or maybe-broadcast)
  let lastIssuedAt = 0;

  /**
   * Reserve the next nonce. MUST be called inside the submit lock, and the
   * caller MUST follow up with commit() (broadcast attempted) or abandon()
   * (nothing was sent).
   */
  async function next() {
    const remoteBig = quantityToBigInt(await rpc.call('eth_getTransactionCount', [address, 'pending']));
    if (remoteBig < 0n || remoteBig > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new Error(`eth_getTransactionCount returned an unusable nonce ${remoteBig} for ${address}`);
    }
    const remote = Number(remoteBig);
    const fresh = lastIssued !== null && now() - lastIssuedAt <= ttlMs;
    if (!fresh || remote > lastIssued) {
      // Chain truth is ahead (or our mark went stale): trust the node. A stale
      // mark plus a lagging remote means our transaction is gone from every
      // mempool, and re-using its nonce is the correct repair.
      if (fresh === false && lastIssued !== null && remote <= lastIssued && logger) {
        logger.warn('nonce_high_water_expired', {
          address, remote_pending: remote, last_issued: lastIssued, ttl_ms: ttlMs,
          detail: 'the RPC still has not seen our last transaction; falling back to chain truth (it was probably dropped)',
        });
      }
      return { nonce: remote, remotePending: remote, source: 'chain' };
    }
    // The RPC is behind our own last broadcast — keep the lane strictly
    // increasing instead of signing a duplicate nonce.
    return { nonce: lastIssued + 1, remotePending: remote, source: 'high_water' };
  }

  /**
   * Record a nonce as consumed. Call as soon as the transaction is SIGNED —
   * before the broadcast, not after. A transport error on the send is an
   * unknown outcome: the transaction may be in a mempool, so its nonce must
   * stay taken or the retry would collide with it.
   */
  function commit(nonce) {
    const n = Number(nonce);
    if (!Number.isInteger(n) || n < 0) throw new Error(`nonce allocator: refusing to commit ${nonce}`);
    if (lastIssued === null || n > lastIssued) lastIssued = n;
    lastIssuedAt = now();
    return lastIssued;
  }

  /**
   * Give a committed nonce back. ONLY legitimate when the node definitively
   * rejected the transaction (a non-transport error): nothing is in flight, so
   * leaving the mark advanced would sign the next transaction into a gap and
   * strand the whole lane behind a nonce that will never be mined.
   */
  function release(nonce) {
    const n = Number(nonce);
    if (lastIssued !== null && n === lastIssued) {
      lastIssued = n > 0 ? n - 1 : null;
      lastIssuedAt = lastIssued === null ? 0 : now();
    }
    return lastIssued;
  }

  /** Forget the mark (crash recovery has just resolved the lane from chain truth). */
  function reset() {
    lastIssued = null;
    lastIssuedAt = 0;
  }

  function state() {
    return { address, lastIssued, lastIssuedAt, ttlMs };
  }

  return { next, commit, release, reset, state };
}

module.exports = { createNonceAllocator, DEFAULT_HIGH_WATER_TTL_MS };
