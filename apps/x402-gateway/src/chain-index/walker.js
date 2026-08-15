'use strict';
/**
 * Head-following walker that fills src/chain-index/store.js: backfills from
 * genesis and then tracks the head. Measured budget from the chain-data
 * recon (2026-08-15): ~170-230 blocks/s at batch-100 => the whole ~73k-block
 * chain backfills in roughly 5-7 minutes, and the incremental follow is
 * trivial (block cadence ~94 s).
 *
 * BEING GENTLE ON THE NODE IS A HARD REQUIREMENT, not a nicety. The node
 * serializes ALL RPC work on one event loop shared with miner getwork and
 * wallets, and this box has prior RPC CPU-starvation incidents. So:
 *
 *   - every request goes through the SAME node client the bulk-export
 *     product uses, whose single-flight rule means the walker can never run
 *     concurrently with a paid export (measured: parallel batches only add
 *     contention, never throughput);
 *   - batches are chunkBlocks (default 100) — a 1000-block batch holds the
 *     node loop 5.8-8.6 s, a 100-block batch ~0.43 s;
 *   - a politeness pause (default 50 ms) yields the loop between chunks;
 *   - the timer is unref()'d, so the walker never keeps a process alive, and
 *     it is started explicitly (src/server.js main()) — NEVER by
 *     createGateway(), which is what the test suite builds. The tests drive
 *     tick() directly against a mocked node.
 *
 * Correctness rules:
 *   - the walk target is head - headMargin, so the index never records a
 *     block that a shallow reorg could still replace;
 *   - continuity is checked (block.parentHash == stored tip hash); a break
 *     rewinds reorgRewind blocks and re-indexes, bounded by maxRewinds per
 *     tick so a pathological chain can never spin;
 *   - `result: null` (beyond head) truncates the chunk, it is never a hole;
 *   - a failed batch aborts the tick at the last COMMITTED height — the next
 *     tick resumes exactly there.
 */

const DEFAULTS = {
  chunkBlocks: 100,
  chunkPauseMs: 50,
  headMargin: 6,
  pollMs: 15000,
  maxLagBlocks: 12,
  batchTimeoutMs: 20000,
  reorgRewind: 64,
  maxRewindsPerTick: 4,
};

function createChainIndexer({ cfg = {}, node, store, logger, sleep, now = Date.now } = {}) {
  const opts = {
    chunkBlocks: cfg.chainIndexChunkBlocks || DEFAULTS.chunkBlocks,
    chunkPauseMs: cfg.chainIndexChunkPauseMs === undefined ? DEFAULTS.chunkPauseMs : cfg.chainIndexChunkPauseMs,
    headMargin: cfg.chainIndexHeadMargin === undefined ? DEFAULTS.headMargin : cfg.chainIndexHeadMargin,
    pollMs: cfg.chainIndexPollMs || DEFAULTS.pollMs,
    maxLagBlocks: cfg.chainIndexMaxLagBlocks === undefined ? DEFAULTS.maxLagBlocks : cfg.chainIndexMaxLagBlocks,
    batchTimeoutMs: cfg.chainIndexBatchTimeoutMs || DEFAULTS.batchTimeoutMs,
    reorgRewind: cfg.chainIndexReorgRewind === undefined ? DEFAULTS.reorgRewind : cfg.chainIndexReorgRewind,
    maxRewindsPerTick: DEFAULTS.maxRewindsPerTick,
  };
  const log = logger || { info() {}, warn() {}, error() {} };
  const pause = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));

  let timer = null;
  let started = false;
  let stopping = false;
  let lastError = null;
  let consecutiveFailures = 0;

  /**
   * One catch-up pass: fetch and commit everything from indexed_height + 1
   * up to head - headMargin, in chunks. Returns a progress summary. Throws
   * only on a head-resolution failure (a chunk failure ends the pass at the
   * last committed height and is reported, not thrown).
   */
  async function tick() {
    const head = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    const headHeight = head && Number.isInteger(head.height) ? head.height : null;
    if (headHeight === null) throw new Error('chain.getHead returned no height');
    const target = headHeight - opts.headMargin;

    let committed = 0;
    let rewinds = 0;
    let truncatedReason = null;
    let st = store.getState();

    while (!stopping && st.indexedHeight < target) {
      const from = st.indexedHeight + 1;
      const to = Math.min(from + opts.chunkBlocks - 1, target);
      const calls = [];
      for (let h = from; h <= to; h++) {
        calls.push({ method: 'chain.getBlockByHeight', params: { height: h, includeTxObjects: true, includeReceipts: false } });
      }

      let results;
      try {
        results = await node.batch(calls, { timeoutMs: opts.batchTimeoutMs });
      } catch (e) {
        // Never a hole: the next tick resumes at the last COMMITTED height.
        truncatedReason = `fetch_failed: ${e.message}`;
        break;
      }

      const blocks = [];
      for (const raw of results) {
        if (raw === null || raw === undefined) break; // beyond head: truncate
        blocks.push(raw);
      }
      if (blocks.length === 0) {
        truncatedReason = 'beyond_head';
        break;
      }

      // Continuity: a parentHash that does not match our stored tip means the
      // chain moved under us. Rewind and re-index rather than stitching two
      // histories together.
      const first = blocks[0];
      if (st.indexedHash && first.parentHash && first.parentHash !== st.indexedHash) {
        if (rewinds >= opts.maxRewindsPerTick) {
          truncatedReason = 'reorg_rewind_limit';
          break;
        }
        rewinds += 1;
        const rewindTo = Math.max(-1, st.indexedHeight - opts.reorgRewind);
        log.warn('chain_index_reorg', {
          at_height: first.number,
          expected_parent: st.indexedHash,
          got_parent: first.parentHash,
          rewind_to: rewindTo,
        });
        store.rollbackAbove(rewindTo);
        st = store.getState();
        continue;
      }

      store.applyBlocks(blocks);
      committed += blocks.length;
      st = store.getState();
      if (blocks.length < calls.length) {
        truncatedReason = 'beyond_head';
        break;
      }
      if (st.indexedHeight < target) await pause(opts.chunkPauseMs);
    }

    store.recordTick({ headHeight, atMs: now(), maxLagBlocks: opts.maxLagBlocks });
    const state = store.getState();
    return {
      head_height: headHeight,
      target_height: target,
      indexed_height: state.indexedHeight,
      blocks_indexed: committed,
      rewinds,
      truncated_reason: truncatedReason,
      lag: headHeight - state.indexedHeight,
    };
  }

  async function runOnce() {
    timer = null;
    if (stopping) return;
    let delay = opts.pollMs;
    try {
      const progress = await tick();
      lastError = null;
      consecutiveFailures = 0;
      if (progress.blocks_indexed > 0) {
        log.info('chain_index_progress', progress);
      }
      // Still backfilling? come straight back rather than idling a poll
      // interval per chunk-run.
      if (progress.indexed_height < progress.target_height && !progress.truncated_reason) delay = 0;
    } catch (e) {
      lastError = e.message;
      consecutiveFailures += 1;
      log.warn('chain_index_tick_failed', { error: e.message, consecutive_failures: consecutiveFailures });
      delay = Math.min(opts.pollMs * Math.min(consecutiveFailures, 4), opts.pollMs * 4);
    }
    if (stopping) return;
    schedule(delay);
  }

  function schedule(delayMs) {
    timer = setTimeout(runOnce, delayMs);
    // Never hold the event loop open: the walker is background work.
    if (timer.unref) timer.unref();
  }

  return {
    tick, // tests + `animica-x402 index backfill`
    options: opts,

    start() {
      if (started) return false;
      started = true;
      stopping = false;
      schedule(0);
      return true;
    },

    stop() {
      stopping = true;
      started = false;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },

    status() {
      const st = store.getState();
      return {
        running: started,
        last_error: lastError,
        consecutive_failures: consecutiveFailures,
        indexed_height: st.indexedHeight,
        head_height: st.headHeight,
        last_tick_ms: st.lastTickMs,
      };
    },
  };
}

module.exports = { createChainIndexer, DEFAULTS };
