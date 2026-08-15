'use strict';
/**
 * Chain address index: sqlite store + head-following walker + the readiness
 * gate the paid history product fails closed on.
 *
 * THE GATE IS THE PRODUCT'S HONESTY CONTRACT. A history answer served from a
 * backfilling or stalled index is not "slightly old", it is WRONG — it omits
 * transactions the buyer paid to see. So availability is refused (503, no
 * 402, nothing charged) with a machine-readable reason whenever:
 *
 *   chain_index_disabled        the operator turned the walker off
 *   chain_index_node_unreachable  the head cannot be resolved right now
 *   chain_index_never_ran       no tick has ever completed
 *   chain_index_walker_stalled  last tick older than maxTickAgeMs
 *   chain_index_backfilling     never caught up yet (progress reported)
 *   chain_index_stale           was live, now lagging > maxLagBlocks
 *
 * Note the lag floor: the walker deliberately stops at head - headMargin
 * (reorg safety), so lag is never below headMargin and maxLagBlocks must
 * exceed it. Config validation enforces that.
 */

const { createChainIndexStore, extractRows, digestOf, amountString } = require('./store');
const { createChainIndexer, DEFAULTS } = require('./walker');

/**
 * Readiness probe shared by the product's availability() hook. `node` is only
 * used for chain.getHead, cached briefly so catalog scrapers cannot hammer
 * the node through us.
 */
function createIndexHealth({ cfg, store, node, now = Date.now, headCacheMs = 5000 }) {
  let headCache = { at: 0, height: null };

  async function head() {
    if (now() - headCache.at < headCacheMs && headCache.height !== null) return headCache.height;
    const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    const height = h && Number.isInteger(h.height) ? h.height : null;
    if (height === null) throw new Error('chain.getHead returned no height');
    headCache = { at: now(), height };
    return height;
  }

  /**
   * -> { ok, reason?, detail?, head_height, indexed_height, lag_blocks,
   *      caught_up, backfilling, progress{} }
   * Never throws: a node failure is an honest `ok:false` with the reason.
   */
  async function check() {
    const st = store.getState();
    const base = {
      indexed_height: st.indexedHeight,
      head_height: st.headHeight,
      last_tick_age_ms: st.lastTickMs ? Math.max(0, now() - st.lastTickMs) : null,
      caught_up: Boolean(st.caughtUpMs),
    };
    if (!cfg.chainIndexEnabled) {
      return Object.assign(base, {
        ok: false,
        reason: 'chain_index_disabled',
        detail: 'the address index walker is disabled (X402_CHAIN_INDEX_ENABLED=0); paid history is not offered without a live index',
      });
    }
    let headHeight;
    try {
      headHeight = await head();
    } catch (e) {
      return Object.assign(base, {
        ok: false,
        reason: 'chain_index_node_unreachable',
        detail: `cannot resolve the chain head to prove the index is current: ${e.message}`,
      });
    }
    base.head_height = headHeight;
    if (!st.lastTickMs) {
      return Object.assign(base, {
        ok: false,
        reason: 'chain_index_never_ran',
        detail: 'the address index has not completed a single pass yet',
        progress: progressOf(st, headHeight),
      });
    }
    const tickAge = now() - st.lastTickMs;
    base.last_tick_age_ms = Math.max(0, tickAge);
    if (tickAge > cfg.chainIndexMaxTickAgeMs) {
      return Object.assign(base, {
        ok: false,
        reason: 'chain_index_walker_stalled',
        detail: `the index walker has not completed a pass for ${Math.round(tickAge / 1000)}s (limit ${Math.round(cfg.chainIndexMaxTickAgeMs / 1000)}s)`,
        progress: progressOf(st, headHeight),
      });
    }
    if (st.indexedHeight < 0) {
      return Object.assign(base, {
        ok: false,
        reason: 'chain_index_backfilling',
        detail: 'the address index is still backfilling from genesis',
        progress: progressOf(st, headHeight),
      });
    }
    const lag = headHeight - st.indexedHeight;
    base.lag_blocks = lag;
    if (lag > cfg.chainIndexMaxLagBlocks) {
      const backfilling = !st.caughtUpMs;
      return Object.assign(base, {
        ok: false,
        reason: backfilling ? 'chain_index_backfilling' : 'chain_index_stale',
        detail: backfilling
          ? `the address index is still backfilling (height ${st.indexedHeight} of ${headHeight})`
          : `the address index is ${lag} blocks behind the head (limit ${cfg.chainIndexMaxLagBlocks})`,
        progress: progressOf(st, headHeight),
      });
    }
    return Object.assign(base, { ok: true, lag_blocks: lag, head_margin: cfg.chainIndexHeadMargin });
  }

  function progressOf(st, headHeight) {
    const indexed = Math.max(0, st.indexedHeight + 1);
    const total = Math.max(1, headHeight + 1);
    return {
      indexed_height: st.indexedHeight,
      head_height: headHeight,
      blocks_indexed: indexed,
      blocks_total: total,
      percent: Math.min(100, Math.round((indexed / total) * 1000) / 10),
    };
  }

  return { check, head };
}

module.exports = {
  createChainIndexStore,
  createChainIndexer,
  createIndexHealth,
  extractRows,
  digestOf,
  amountString,
  DEFAULTS,
};
