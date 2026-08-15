'use strict';
/**
 * Serving-capacity gate for priority inference. The chain cannot enumerate
 * inference workers globally (there is no aicf.listServingWorkers;
 * aicf.work.listWorkers is a different, stale registry; and
 * aicf.estimateJobCost.providers counts unpruned registration history with
 * no freshness filter — 210 "providers" while the true count was 0). The
 * ground truth primitive is per-wallet `aicf.workerStatus`, the same one the
 * production chat bridge polls over its operator-maintained wallet list —
 * gate and router MUST share one view, or the gate sells priority the
 * router can't deliver.
 *
 * Count = distinct configured wallets where registered === true AND
 * now - last_seen <= 300 s (values > 1e12 are milliseconds — normalize) AND
 * the configured chain tier is in Set(tiers) minus the synthetic "pipeline"
 * flag (tiers arrive with duplicates and that flag).
 *
 * Fail-closed threshold semantics:
 *   available() === enabled && count >= minWorkers && probe fresh (<= maxAge)
 * A probe error contributes 0 for that wallet; a wholly-failed or stale
 * probe makes the gate unavailable. Never fail open.
 */

const FRESH_WINDOW_S = 300;

function normalizeLastSeenSec(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n > 1e12 ? n / 1000 : n;
}

function createCapacityGate({
  rpcUrl,
  wallets = [],
  tier = 'standard',
  minWorkers = 2,
  enabled = false,
  fetchImpl = fetch,
  probeTimeoutMs = 3000,
  probeIntervalMs = 15000,
  maxProbeAgeMs = 60000,
  now = Date.now,
  logger = console,
} = {}) {
  let lastCount = 0;
  let lastProbeAt = 0; // 0 = never probed => unavailable (fail closed)
  let timer = null;

  async function workerStatus(address) {
    const res = await fetchImpl(rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'aicf.workerStatus', params: { address } }),
      signal: AbortSignal.timeout(probeTimeoutMs),
    });
    if (!res.ok) throw new Error(`http ${res.status}`);
    const out = JSON.parse(await res.text());
    if (out.error) throw new Error(out.error.message || 'rpc error');
    return out.result;
  }

  function isServing(status, nowSec) {
    if (!status || status.registered !== true) return false;
    const seen = normalizeLastSeenSec(status.last_seen);
    if (seen === null || nowSec - seen > FRESH_WINDOW_S) return false;
    const tiers = new Set((Array.isArray(status.tiers) ? status.tiers : []).filter((t) => t !== 'pipeline'));
    return tiers.has(tier);
  }

  /** One full probe sweep (sequential — a handful of loopback calls). */
  async function probeOnce() {
    const nowSec = now() / 1000;
    let count = 0;
    for (const w of wallets) {
      try {
        if (isServing(await workerStatus(w), nowSec)) count += 1;
      } catch (e) {
        // Unreachable/erroring wallet counts as not serving. Never throw:
        // an all-failed sweep records count 0, which is the correct gate.
        logger.debug && logger.debug('capacity_probe_wallet_failed', { wallet: w, error: e.message });
      }
    }
    lastCount = count;
    lastProbeAt = now();
    return count;
  }

  function snapshot() {
    return {
      enabled,
      tier,
      serving_workers: lastCount,
      required: minWorkers,
      probe_age_ms: lastProbeAt ? now() - lastProbeAt : null,
      wallets_configured: wallets.length,
    };
  }

  function available() {
    if (!enabled) return false;
    if (!lastProbeAt || now() - lastProbeAt > maxProbeAgeMs) return false; // stale probe => 0
    return lastCount >= minWorkers;
  }

  function start() {
    if (timer || !enabled) return;
    timer = setInterval(() => {
      probeOnce().catch((e) => logger.warn && logger.warn('capacity_probe_failed', { error: e.message }));
    }, probeIntervalMs);
    if (timer.unref) timer.unref();
    // Fire one immediately so the gate can open without waiting a full tick.
    probeOnce().catch((e) => logger.warn && logger.warn('capacity_probe_failed', { error: e.message }));
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  return { probeOnce, snapshot, available, start, stop, get count() { return lastCount; } };
}

module.exports = { createCapacityGate, normalizeLastSeenSec, FRESH_WINDOW_S };
