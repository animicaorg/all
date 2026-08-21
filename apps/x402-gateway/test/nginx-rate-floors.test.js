'use strict';
/**
 * Rate-limit floors for the x402 nginx config.
 *
 * These numbers are not style preferences — they were measured. On 2026-08-19
 * the probe zone at 60r/s with burst=200 answered 429 to 7,479 requests in one
 * day, because agent clients sweeping the catalog peak at ~115 req/s from a
 * single IP. Directories (x402-observer, AgentScore, mri-indexer, CarbonMonitor)
 * read exactly those surfaces to SCORE us, and a 429 is recorded as
 * unreliability by the systems agents use to decide who to pay.
 *
 * The repo template had silently drifted BELOW the deployed values, so anyone
 * deploying it wholesale would have reintroduced the throttling that was the
 * real blocker on x402 demand. This test exists so that cannot happen quietly.
 *
 * Raising a floor here is fine when a new measurement justifies it. LOWERING one
 * needs evidence that the load it was sized for has gone away.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ZONES = path.join(__dirname, '..', 'nginx', 'animica-x402-zones.conf');
const LOCATIONS = path.join(__dirname, '..', 'nginx', 'animica-dev-x402.conf');

/** Minimum sustained rate, requests/second, per zone. */
const RATE_FLOOR = {
  x402_catalog: 300,   // machine-readable listing + healthz: cheap JSON, never touches money
  x402_probe: 300,     // unpaid 402 challenges: ~2.6x the highest burst observed here
};

/**
 * Minimum burst, but only on the surfaces a directory actually READS and scores:
 * the machine-readable catalog, the discovery documents and the health probe.
 *
 * Deliberately NOT applied everywhere the zone appears. /x402/scan and
 * /x402/bounty accept submissions and /x402/crawl is a decision API; all three
 * carry tighter bursts on purpose, and measured 2026-08-20 they take single-digit
 * daily requests with zero 429s. Raising them would be copying a number rather
 * than sizing for load.
 */
const SCORED_SURFACES = [
  '= /x402',
  '= /x402/healthz',
  '^~ /x402/',
  '~ ^/(\\.well-known/x402(-services)?(\\.json)?|x402\\.json|api/x402)$',
];
const BURST_FLOOR = { x402_catalog: 600, x402_probe: 600 };

function parseRates(text) {
  const out = {};
  const re = /limit_req_zone\s+\S+\s+zone=(\w+):\w+\s+rate=(\d+)(r\/s|r\/m)/g;
  let m;
  while ((m = re.exec(text))) {
    const perSecond = m[3] === 'r/m' ? Number(m[2]) / 60 : Number(m[2]);
    out[m[1]] = perSecond;
  }
  return out;
}

test('zone rates are at or above the measured floor', () => {
  const rates = parseRates(fs.readFileSync(ZONES, 'utf8'));
  for (const [zone, floor] of Object.entries(RATE_FLOOR)) {
    assert.ok(rates[zone] !== undefined, `zone ${zone} is not defined at all`);
    assert.ok(
      rates[zone] >= floor,
      `zone ${zone} is ${rates[zone]}r/s, below the measured floor of ${floor}r/s — `
      + 'agent sweeps peak at ~115 req/s per IP and a 429 is scored as unreliability',
    );
  }
});

test('the surfaces directories score are at or above their burst floor', () => {
  const text = fs.readFileSync(LOCATIONS, 'utf8');
  // Split into location blocks so a burst can be attributed to its own surface.
  const blocks = text.split(/^location\s+/m).slice(1);
  const violations = [];
  for (const block of blocks) {
    const header = block.slice(0, block.indexOf('{')).trim();
    if (!SCORED_SURFACES.includes(header)) continue;
    const re = /limit_req\s+zone=(\w+)\s+burst=(\d+)/g;
    let m;
    while ((m = re.exec(block))) {
      const floor = BURST_FLOOR[m[1]];
      if (floor && Number(m[2]) < floor) {
        violations.push(`${header}: ${m[1]} burst=${m[2]} < ${floor}`);
      }
    }
  }
  assert.deepEqual(violations, [],
    'a burst below the floor drains in seconds under a real catalog sweep');
});

test('the scored surfaces all still exist under those exact location headers', () => {
  // Guards the test above from silently passing because a location was renamed.
  const text = fs.readFileSync(LOCATIONS, 'utf8');
  for (const surface of SCORED_SURFACES) {
    assert.ok(text.includes(`location ${surface} {`),
      `scored surface "${surface}" is no longer in the config — update SCORED_SURFACES`);
  }
});

test('the probe zone keys on something other than the bare client IP', () => {
  // Probes arrive from a handful of IPs sending thousands of requests. Keying
  // the probe zone purely on $binary_remote_addr would let one busy monitor
  // throttle every other agent behind the same key.
  const text = fs.readFileSync(ZONES, 'utf8');
  const m = text.match(/limit_req_zone\s+(\S+)\s+zone=x402_probe:/);
  assert.ok(m, 'x402_probe zone not found');
  assert.notEqual(m[1], '$binary_remote_addr',
    'x402_probe should key on $x402_probe_key, not the raw client address');
});

test('the PAID zone stays tight — money paths are not what needed loosening', () => {
  const rates = parseRates(fs.readFileSync(ZONES, 'utf8'));
  assert.ok(rates.x402_paid !== undefined, 'x402_paid zone missing');
  assert.ok(
    rates.x402_paid <= 60,
    `x402_paid is ${rates.x402_paid}r/s — the throttling problem was on UNPAID probes; `
    + 'the settlement path should stay narrow',
  );
});
