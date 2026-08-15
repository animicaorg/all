'use strict';
/**
 * ADVERSARIAL REVIEW 3/3 — vector (5) payment accepted while service
 * unavailable.
 *
 * Two probes:
 *   A. QRNG (the "beacon") is execute-then-settle: the bytes are fetched and
 *      health-checked in handler() BEFORE settlement. Killing the beacon
 *      between the readiness check and settle makes handler() throw and NO
 *      payment is taken. This is the CORRECT ordering — asserted as a
 *      regression guard (negative result).
 *   B. Priority inference gates on a CACHED capacity probe. The mandatory
 *      pre-settle re-check (priority-inference.preSettle) reads capacity
 *      .available(), which is only refreshed by a 15 s background timer and
 *      considered "fresh" for up to X402_CAPACITY_MAX_PROBE_AGE_MS (60 s).
 *      Kill every worker AFTER the last successful probe and the gate keeps
 *      reporting available for up to 60 s — so preSettle passes and the
 *      gateway settles for a dead pool. This contradicts the module's own
 *      claim that "a worker drop between the 402 and the paid retry costs the
 *      payer nothing." (Recovered only via the signed-receipt refund path.)
 */

const test = require('node:test');
const assert = require('node:assert');

const { createCapacityGate } = require('../src/capacity');
const { createPriorityInferenceProduct } = require('../src/products/priority-inference');
const { createQrngProduct } = require('../src/products/qrng');
const { ProductUnavailable } = require('../src/products/errors');

/* ---- A. QRNG beacon: killing it before settle takes no money (GOOD) ---- */

test('QRNG: beacon killed before settle -> handler throws, NO settlement (correct order)', async () => {
  let alive = true;
  const node = {
    async call(method) {
      assert.equal(method, 'rand.quantumRandomBytes');
      if (!alive) throw new Error('beacon down');
      return {
        bytes_hex: 'ab'.repeat(8), n: 8,
        source: 'software-fallback',
        health: { passed: true, min_entropy_per_byte: 7.9 },
        attestation: { attested: false },
      };
    },
  };
  const cfg = { qrngTimeoutMs: 1000, qrngMaxBytes: 1024, qrngPriceUsd: '0.01', qrngEnabled: true };
  const qrng = createQrngProduct({ cfg, node });

  // readiness passes while alive
  assert.equal((await qrng.availability()).available, true);

  // beacon dies between readiness and the paid retry's handler
  alive = false;

  // execute-then-settle: handler runs BEFORE settle; it throws, so the
  // paywall answers 503 and never calls the facilitator. Money-safe.
  await assert.rejects(() => qrng.handler({ params: { n: 8 } }), (e) => {
    assert.ok(e instanceof ProductUnavailable, 'ProductUnavailable, not a settled charge');
    return true;
  });
  assert.equal(qrng.mode, 'execute-then-settle');
});

/* ---- B. Inference capacity gate: stale-probe window opens the gate ---- */

function servingStatus(nowSec) {
  return {
    registered: true,
    last_seen: nowSec,               // seconds, fresh
    tiers: ['standard', 'pipeline'], // includes the configured tier
  };
}

test('POC: capacity gate reports available from a STALE probe after all workers die', async () => {
  let clock = 10_000_000; // ms
  const now = () => clock;
  let workersAlive = true;

  const fetchImpl = async () => {
    if (!workersAlive) throw new Error('worker RPC unreachable (pool dead)');
    return {
      ok: true,
      status: 200,
      async text() {
        return JSON.stringify({ result: servingStatus(Math.floor(clock / 1000)) });
      },
    };
  };

  const gate = createCapacityGate({
    rpcUrl: 'http://127.0.0.1/rpc',
    wallets: ['anim1aaaaaaaaaa', 'anim1bbbbbbbbbb'],
    tier: 'standard',
    minWorkers: 2,
    enabled: true,
    fetchImpl,
    maxProbeAgeMs: 60000,
    now,
    logger: { debug() {}, warn() {} },
  });

  // One successful probe: two live workers -> gate open.
  await gate.probeOnce();
  assert.equal(gate.available(), true, 'gate open after a healthy probe');

  // Every worker vanishes right after the probe. No re-probe happens yet
  // (background timer would only fire after probeIntervalMs).
  workersAlive = false;

  // The mandatory pre-settle re-check reads the SAME cached view.
  const product = createPriorityInferenceProduct({
    cfg: { inferencePriceUsd: '0.10', inferenceMaxBodyBytes: 1024, inferenceTimeoutMs: 1000, inferenceUpstreamUrl: 'http://127.0.0.1/none' },
    capacity: gate,
    fetchImpl: async () => ({ status: 200, text: async () => '{}', headers: { get: () => 'application/json' } }),
  });

  // Advance well within the freshness window (30 s < 60 s max age).
  clock += 30_000;

  // The cached view is still "open" — that is the trap this test guards.
  assert.equal(gate.available(), true, 'cached view is stale-open for up to maxProbeAgeMs');

  // FIXED: preSettle no longer trusts the cache. It forces a synchronous probe
  // immediately before the payer's USDC moves, so a pool that died inside the
  // freshness window is caught and the payment is refused (fail closed).
  await assert.rejects(
    () => product.preSettle(),
    (err) => err && err.unavailable === true && err.status === 503,
    'preSettle re-probes and refuses payment when the pool is dead'
  );

  // And the forced probe has updated the shared view too.
  assert.equal(gate.available(), false, 'the pre-settle probe closed the gate');
});

test('the stale-probe gate eventually fails closed once maxProbeAge elapses without a probe', async () => {
  let clock = 20_000_000;
  const now = () => clock;
  const fetchImpl = async () => ({
    ok: true, status: 200, async text() { return JSON.stringify({ result: servingStatus(Math.floor(clock / 1000)) }); },
  });
  const gate = createCapacityGate({
    rpcUrl: 'http://127.0.0.1/rpc', wallets: ['anim1aaaaaaaaaa', 'anim1bbbbbbbbbb'],
    tier: 'standard', minWorkers: 2, enabled: true, fetchImpl, maxProbeAgeMs: 60000, now,
    logger: { debug() {}, warn() {} },
  });
  await gate.probeOnce();
  assert.equal(gate.available(), true);
  clock += 61_000; // exceed maxProbeAgeMs with no new probe
  assert.equal(gate.available(), false, 'gate closes once the probe is older than maxProbeAgeMs');
});
