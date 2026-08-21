'use strict';
/**
 * Solve tests.
 *
 * The dangerous failure for a planner is not crashing — it is producing a
 * confident, plausible, wrong plan. So the tests that matter here are: a poor
 * match becomes a named gap rather than a step; a plan never exceeds the stated
 * budget; unbuyable services are never planned; and the endpoint never claims
 * to have executed or verified anything.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { createSolveProduct } = require('../src/products/solve');
const { loadGatewayConfig } = require('../src/config');

const cfg = loadGatewayConfig({});

function svc(over = {}) {
  return {
    key: over.resource || 'a.example/x',
    resource: 'https://a.example/x',
    description: 'a service',
    price_usd: 0.01,
    network: 'eip155:8453',
    pay_to: '0x1',
    calls_30d: 10,
    unique_payers_30d: 9,
    call_spec: { method: 'POST' },
    sources: ['bazaar'],
    ...over,
  };
}

function stubIndex(records) {
  return { getIndex: async () => ({ at: Date.now(), records, counts: { probe: { paywalled: 0 } } }) };
}

/** A model stub that returns a fixed decomposition. */
function modelReturning(steps) {
  return async (url) => ({
    ok: true, status: 200, headers: { get: () => 'application/json' },
    text: async () => '',
    json: async () => ({ model: 'stub', choices: [{ message: { content: JSON.stringify({ steps }) } }] }),
  });
}

async function plan(records, steps, body = {}) {
  const p = createSolveProduct({ cfg, indexCache: stubIndex(records), fetchImpl: modelReturning(steps) });
  const out = await p.handler({ params: p.validate({ json: { goal: 'do the thing described here', ...body } }) });
  assert.equal(out.status, 200);
  return out.bodyObj;
}

test('a poor match is a named gap, not a confident step', async () => {
  // The live failure this pins: "verify company legitimacy" was answered with
  // an email-address validator and presented as a completed plan.
  // The candidate must SHARE a word to reach the coverage check at all —
  // something with no overlap is filtered by relevance first, which is a
  // different (also correct) branch.
  const d = await plan(
    [svc({ description: 'Validate that an email address exists and can receive mail' })],
    [{ capability: 'verify company registration address and legitimacy', why: 'check the business is real' }],
  );
  assert.equal(d.plan.steps_planned, 0);
  assert.equal(d.gaps.length, 1);
  assert.match(d.gaps[0].reason, /word coverage, below the/);
  assert.ok(d.gaps[0].closest, 'the near-miss is named so the caller can judge for themselves');
  assert.equal(d.plan.complete, false);
});

test('a weak but admissible match is labelled, not presented as certain', async () => {
  // Half the capability's words appear in the candidate: admissible, but a
  // property-risk service answering a crypto question is exactly the match a
  // caller must be warned about.
  const d = await plan(
    [svc({ description: 'risk score for a property address' })],
    [{ capability: 'assess risk score crypto wallet address', why: 'score it' }],
  );
  if (d.plan.steps_planned) {
    assert.ok(['low', 'medium'].includes(d.plan.steps[0].chosen.match_confidence));
    assert.ok(d.plan.steps[0].chosen.match_warning, 'a weak match must carry its warning');
  } else {
    assert.equal(d.gaps.length, 1);
  }
});

test('the plan never exceeds the stated budget, and says what it dropped', async () => {
  const records = [
    svc({ resource: 'https://a.example/one', key: 'a.example/one', description: 'translate documents between languages', price_usd: 0.30 }),
    svc({ resource: 'https://b.example/two', key: 'b.example/two', description: 'summarize documents into bullet points', price_usd: 0.30 }),
  ];
  const d = await plan(records, [
    { capability: 'translate documents between languages', why: 'translate' },
    { capability: 'summarize documents into bullet points', why: 'summarise' },
  ], { max_budget_usd: 0.35 });
  assert.equal(d.plan.steps_planned, 1);
  assert.ok(d.plan.total_cost_usd <= 0.35);
  assert.equal(d.plan.within_budget, true);
  assert.equal(d.gaps.length, 1);
  assert.match(d.gaps[0].reason, /of the \$0\.3500 budget remains/);
});

test('services that cannot be bought are never planned', async () => {
  const records = [
    svc({ resource: 'https://no-spec.example/x', key: 'no-spec.example/x', description: 'translate documents between languages', call_spec: null }),
    svc({ resource: 'https://dead.example/x', key: 'dead.example/x', description: 'translate documents between languages', probe: { outcome: 'dead' } }),
    svc({ resource: 'https://open.example/x', key: 'open.example/x', description: 'translate documents between languages', probe: { outcome: 'open' } }),
  ];
  const d = await plan(records, [{ capability: 'translate documents between languages', why: 'x' }]);
  assert.equal(d.plan.steps_planned, 0, 'no request shape, a dead listing and an unpaywalled one are all unbuyable');
  assert.equal(d.pool.callable_and_buyable, 0);
});

test('excluded hosts are honoured', async () => {
  const records = [svc({ description: 'translate documents between languages' })];
  const d = await plan(records, [{ capability: 'translate documents between languages', why: 'x' }], { exclude_hosts: ['a.example'] });
  assert.equal(d.plan.steps_planned, 0);
});

test('require_verified narrows the pool to services we have actually called', async () => {
  const records = [
    svc({ resource: 'https://unv.example/x', key: 'unv.example/x', description: 'translate documents between languages' }),
    svc({ resource: 'https://ver.example/x', key: 'ver.example/x', description: 'translate documents between languages', probe: { outcome: 'paywalled' } }),
  ];
  const loose = await plan(records, [{ capability: 'translate documents between languages', why: 'x' }]);
  assert.equal(loose.pool.callable_and_buyable, 2);
  const strict = await plan(records, [{ capability: 'translate documents between languages', why: 'x' }], { require_verified: true });
  assert.equal(strict.pool.callable_and_buyable, 1);
  assert.equal(strict.plan.steps[0].chosen.resource, 'https://ver.example/x');
});

test('the response never claims to have executed or verified the pipeline', async () => {
  const d = await plan(
    [svc({ description: 'translate documents between languages' }), svc({ resource: 'https://b.example/y', key: 'b.example/y', description: 'summarize documents into bullet points' })],
    [
      { capability: 'translate documents between languages', why: 'a' },
      { capability: 'summarize documents into bullet points', why: 'b', depends_on: [0] },
    ],
  );
  assert.equal(d.execution.performed, false);
  assert.match(d.execution.why_not, /does not spend/);
  for (const s of d.plan.steps) assert.equal(s.dataflow.verified, false, 'no edge may ever be claimed as verified');
  assert.ok(d.caveats.some((c) => /DATAFLOW BETWEEN STEPS IS NOT VERIFIED/.test(c)));
  assert.ok(d.caveats.some((c) => /Nothing here was purchased/.test(c)));
});

test('budget and step bounds are enforced at validate time', () => {
  const p = createSolveProduct({ cfg, indexCache: stubIndex([]), fetchImpl: modelReturning([]) });
  assert.throws(() => p.validate({ json: { goal: 'short' } }), /goal is required/);
  assert.throws(() => p.validate({ json: { goal: 'a real goal here', max_budget_usd: 0 } }), /positive number/);
  assert.throws(() => p.validate({ json: { goal: 'a real goal here', max_budget_usd: 1e9 } }), /capped at/);
  assert.throws(() => p.validate({ json: { goal: 'a real goal here', max_steps: 99 } }), /max_steps must be/);
});
