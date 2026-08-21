'use strict';
/**
 * POST /x402/solve — compile a goal into a concrete, priced plan of x402 calls.
 *
 * An agent describes what it wants and what it can spend. Animica decomposes
 * the goal into capabilities, searches the merged x402 index for a real service
 * that can perform each one, checks the budget arithmetic, and returns an
 * executable plan: exact URLs, methods, request shapes, prices, alternates, and
 * an honest account of what it could not plan.
 *
 * IT DOES NOT SPEND. This version plans and stops.
 *
 * That is a deliberate line, not an unfinished feature. Executing the plan
 * means Animica holding a funded wallet and paying strangers on a caller's
 * behalf, inside a loop driven by a model, against endpoints we did not write.
 * The planning intelligence is worth having on its own — the caller can execute
 * the plan with their own wallet, on their own terms, and can read it first.
 * Turning that into autonomous spending is an explicit decision with a hard
 * spend cap behind it, not something to arrive at by accident.
 *
 * WHAT MAKES A PLAN HONEST HERE:
 *
 *  - Only CALLABLE services are planned. A step whose only candidates publish
 *    no request shape is left unplanned and named, because "here is a URL, good
 *    luck" is not a plan.
 *  - Prices are the merchant's own where we have probed them, and flagged as a
 *    directory claim where we have not.
 *  - The budget is checked against the SUM of the plan, and a plan that does not
 *    fit is returned truncated with the gap stated — never silently trimmed to
 *    look affordable.
 *  - **Dataflow between steps is NOT verified.** With ~5% of the economy
 *    publishing schemas, we usually cannot prove step 2 accepts what step 1
 *    emits. Every edge says whether it was checked, and the overwhelming
 *    majority say no. Claiming a verified pipeline we cannot verify would be
 *    the single most damaging thing this endpoint could do.
 */

const { ProductError } = require('./errors');
const { createEngine } = require('./structured');
const M = require('./mesh-index');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

const DECOMPOSE_SCHEMA = {
  type: 'object',
  required: ['steps'],
  additionalProperties: false,
  properties: {
    steps: {
      type: 'array',
      minItems: 1,
      maxItems: 8,
      items: {
        type: 'object',
        required: ['capability', 'why'],
        additionalProperties: false,
        properties: {
          capability: {
            type: 'string', minLength: 4, maxLength: 160,
            description: 'the CAPABILITY needed, phrased as a service would describe itself (e.g. "extract structured data from a web page"), not as an instruction',
          },
          why: { type: 'string', maxLength: 200, description: 'one line: what this step contributes to the goal' },
          depends_on: {
            type: 'array', maxItems: 8,
            items: { type: 'integer', minimum: 0 },
            description: 'zero-based indexes of earlier steps whose output this one needs',
          },
        },
      },
    },
  },
};


/**
 * How much of the requested capability the candidate's own text actually
 * covers, as a fraction of the capability's distinct words.
 *
 * BM25 always returns a best match — that is what ranking does — but "best of
 * 563" is not "suitable". Without a floor the planner cheerfully answered
 * "verify company legitimacy" with an email-address validator and presented it
 * as a completed plan. A named gap is worth more than a confident wrong step.
 */
function coverage(capabilityTokens, record) {
  const want = new Set(capabilityTokens);
  if (!want.size) return 0;
  const have = new Set(M.tokens(`${record.description} ${record.resource}`));
  let hit = 0;
  for (const w of want) if (have.has(w)) hit++;
  return hit / want.size;
}


/**
 * A plain label for how well a step's chosen service matches what was asked.
 *
 * The coverage floor keeps out the worst matches, but a number just above the
 * floor is still a guess, and a plan that presents "0.5" and "1.0" identically
 * invites a caller to trust both equally. Naming the weak ones costs nothing
 * and is the difference between a shortlist and a claim.
 */
function matchConfidence(cov) {
  if (cov >= 0.85) return { level: 'high', note: null };
  if (cov >= 0.65) return { level: 'medium', note: 'the service covers most of what this step asks for, but check it does the specific thing you meant' };
  return { level: 'low', note: 'this barely cleared the relevance floor — it shares words with the capability but may be for a different domain entirely. Verify before relying on it, or treat this step as unsolved.' };
}

/** A step's chosen provider plus the runners-up, so a caller can substitute. */
function shapeCandidate(s) {
  const r = s.record;
  return {
    resource: r.resource,
    description: r.description.slice(0, 240),
    price_usd: r.price_usd,
    price_source: r.probe && r.probe.outcome === 'paywalled' ? 'the merchant\'s own 402, read by us' : 'the directory listing, unverified by us',
    network: r.network,
    pay_to: r.pay_to,
    call_spec: r.call_spec,
    verified: r.probe ? r.probe.outcome : null,
    demand: { calls_30d: r.calls_30d, unique_payers_30d: r.unique_payers_30d },
    score: Math.round(s.total * 1000) / 1000,
  };
}

function createSolveProduct({ cfg, indexCache, fetchImpl = fetch, now = Date.now, logger = null }) {
  const engine = createEngine({ cfg, fetchImpl, now });

  return {
    id: 'solve_plan',
    title: 'x402 Solve — compile a goal into a priced plan of real API calls',
    description:
      "Describe a goal and a budget; get back a concrete plan of real x402 calls that would accomplish it — exact URLs, HTTP methods, request shapes, per-step prices from the merchants' own 402 challenges where we have verified them, alternates for every step, and a total you can check before committing. Only services that publish a callable request shape are planned; a step with no invokable candidate is returned as an explicit gap rather than a URL and a shrug. IT DOES NOT SPEND: this compiles the plan and stops, so you execute it with your own wallet on your own terms. It also does not claim the pipeline type-checks — roughly 5% of the x402 economy publishes schemas, so most step-to-step handoffs cannot be verified, and every edge says which it is.",
    path: '/x402/solve',
    routes: [{ method: 'POST', path: '/x402/solve' }],
    priceUsd: cfg.solvePriceUsd,
    enabled: cfg.solveEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 32 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          goal: { type: 'string', required: true, description: 'what you want accomplished, in plain words' },
          max_budget_usd: { type: 'number', required: false, description: `total you are willing to spend executing the plan (default ${cfg.solveDefaultBudgetUsd})` },
          max_steps: { type: 'integer', required: false, description: `cap the plan length, 1..${cfg.solveMaxSteps}` },
          require_verified: { type: 'boolean', required: false, description: 'only plan services we have called ourselves and confirmed answer 402 (default false — it is a much smaller pool)' },
          exclude_hosts: { type: 'array', required: false, description: 'hostnames never to plan against' },
        },
      },
      output: {
        type: 'json',
        description:
          'plan {steps[] {index, capability, why, depends_on, chosen, alternatives[], dataflow}, total_cost_usd, within_budget, steps_planned, steps_unplanned}, gaps[], execution {performed: false, how_to_run}, caveats[]',
      },
    },

    async availability() {
      // The decomposer is a hard dependency here: without it there is no plan,
      // and half a plan is not a cheaper plan.
      const h = await engine.health();
      return h.available ? { available: true } : h;
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.goal !== 'string' || b.goal.trim().length < 8) {
        throw bad('goal is required: describe in plain words what you want accomplished', 'invalid_request');
      }
      let budget = Number(cfg.solveDefaultBudgetUsd);
      if (b.max_budget_usd !== undefined) {
        const v = Number(b.max_budget_usd);
        if (!Number.isFinite(v) || v <= 0) throw bad('max_budget_usd must be a positive number', 'invalid_request');
        if (v > Number(cfg.solveMaxBudgetUsd)) {
          throw bad(`max_budget_usd is capped at $${cfg.solveMaxBudgetUsd} for a plan; nothing is spent, but a plan larger than that is not a plan, it is a shopping spree`, 'invalid_request');
        }
        budget = v;
      }
      let maxSteps = Number(cfg.solveMaxSteps);
      if (b.max_steps !== undefined) {
        if (!Number.isInteger(b.max_steps) || b.max_steps < 1 || b.max_steps > Number(cfg.solveMaxSteps)) {
          throw bad(`max_steps must be an integer between 1 and ${cfg.solveMaxSteps}`, 'invalid_request');
        }
        maxSteps = b.max_steps;
      }
      let exclude = [];
      if (b.exclude_hosts !== undefined) {
        if (!Array.isArray(b.exclude_hosts)) throw bad('exclude_hosts must be an array of hostnames', 'invalid_request');
        exclude = b.exclude_hosts.map((h) => String(h).toLowerCase().replace(/^www\./, '')).slice(0, 100);
      }
      return {
        goal: b.goal.trim().slice(0, 1000),
        budget,
        maxSteps,
        requireVerified: b.require_verified === true,
        exclude,
      };
    },

    async handler(ctx) {
      const { goal, budget, maxSteps, requireVerified, exclude } = ctx.params;
      const index = await indexCache.getIndex();

      // ---- 1. Decompose the goal into capabilities ------------------------
      let decomposed;
      try {
        const out = await engine.structured({
          instruction:
            'Break this goal into the minimum sequence of distinct CAPABILITIES that separate paid web APIs would each provide. '
            + 'Phrase each capability as a short natural-language phrase the way a service would describe itself ("extract structured data from a web page"), in plain words with spaces. NEVER use snake_case, camelCase or an identifier. '
            + 'Prefer fewer steps. Do not invent steps that the goal does not require. '
            + 'Use depends_on only where a step genuinely needs an earlier step\'s output.',
          input: `GOAL: ${goal}`,
          schema: DECOMPOSE_SCHEMA,
          maxTokens: 900,
        });
        decomposed = out.data.steps.slice(0, maxSteps);
      } catch (e) {
        // A ProductError from the engine already carries a useful body.
        if (e instanceof ProductError) throw e;
        throw bad(`the goal could not be decomposed into capabilities: ${e.message}`, 'decompose_failed');
      }

      // ---- 2. Match each capability against the index ---------------------
      // Callability is required: a step whose candidates publish no request
      // shape is a gap, not a plan item.
      const excluded = new Set(exclude);
      const usable = index.records.filter((r) => {
        if (!r.call_spec) return false;
        if (requireVerified && !(r.probe && r.probe.outcome === 'paywalled')) return false;
        if (r.probe && r.probe.outcome !== 'paywalled') return false;   // open/dead/error are not buyable
        try { if (excluded.has(new URL(r.resource).hostname.replace(/^www\./, ''))) return false; } catch { return false; }
        return true;
      });
      const bm25 = M.buildBm25(usable);

      const steps = [];
      const gaps = [];
      let running = 0;
      for (let i = 0; i < decomposed.length; i++) {
        const d = decomposed[i];
        const q = M.tokens(d.capability);
        const { scored } = M.rank(usable, bm25, q, { maxPriceUsd: null, requireCallable: true });
        const priced = scored.filter((s) => s.record.price_usd !== null && s.record.price_usd > 0);
        if (!priced.length) {
          gaps.push({
            step: i,
            capability: d.capability,
            reason: scored.length
              ? 'candidates exist but none publishes a usable price, so this step cannot be budgeted'
              : `no service in the ${usable.length} callable services indexed matches this capability`,
            suggestion: 'Try different wording — matching is lexical — or drop require_verified to widen the pool.',
          });
          continue;
        }
        // Apply the floor before anything else: a poor match is a gap.
        const withCoverage = priced.map((c) => ({ c, cov: coverage(q, c.record) }));
        const good = withCoverage.filter((x) => x.cov >= Number(cfg.solveMinCoverage));
        if (!good.length) {
          const best = withCoverage[0];
          gaps.push({
            step: i,
            capability: d.capability,
            reason: `nothing indexed covers this capability well enough to plan. The closest was "${best.c.record.description.slice(0, 90)}" at ${Math.round(best.cov * 100)}% word coverage, below the ${Math.round(Number(cfg.solveMinCoverage) * 100)}% floor.`,
            suggestion: 'A wrong step presented confidently is worse than a missing one. Reword the goal, or accept that this capability is not purchasable on x402 yet.',
            closest: { resource: best.c.record.resource, coverage: Math.round(best.cov * 100) / 100 },
          });
          continue;
        }
        const chosen = good[0].c;
        const chosenCoverage = good[0].cov;
        const cost = chosen.record.price_usd;
        // Budget is checked against the running total, and a step that does not
        // fit is reported as a gap rather than quietly dropped.
        if (running + cost > budget) {
          gaps.push({
            step: i,
            capability: d.capability,
            reason: `the cheapest usable option costs $${cost.toFixed(4)}, and $${(budget - running).toFixed(4)} of the $${budget.toFixed(4)} budget remains`,
            suggestion: 'Raise max_budget_usd, or drop an earlier step.',
            cheapest_usd: cost,
          });
          continue;
        }
        running += cost;

        // Dataflow: only claimable when BOTH ends publish a shape.
        const deps = Array.isArray(d.depends_on) ? d.depends_on.filter((n) => n < i) : [];
        const upstreamShaped = deps.every((n) => {
          const prev = steps.find((s) => s.index === n);
          return prev && prev.chosen && prev.chosen.call_spec;
        });
        steps.push({
          index: i,
          capability: d.capability,
          why: d.why,
          depends_on: deps,
          chosen: {
            ...shapeCandidate(chosen),
            capability_coverage: Math.round(chosenCoverage * 100) / 100,
            match_confidence: matchConfidence(chosenCoverage).level,
            match_warning: matchConfidence(chosenCoverage).note,
          },
          alternatives: good.slice(1, 4).map((x) => ({ ...shapeCandidate(x.c), capability_coverage: Math.round(x.cov * 100) / 100 })),
          dataflow: deps.length === 0 ? {
            checked: false, verified: false,
            note: 'no upstream step — this one takes your input directly',
          } : {
            checked: Boolean(upstreamShaped),
            verified: false,
            note: upstreamShaped
              ? 'both ends publish a request shape, but we have not executed either, so compatibility is inferred from declarations rather than observed'
              : 'an upstream step publishes no output shape, so nothing about this handoff can be checked — you will have to adapt the data yourself',
          },
        });
      }

      const withinBudget = running <= budget;
      return { status: 200, bodyObj: {
        product: 'solve_plan',
        goal,
        plan: {
          steps,
          steps_planned: steps.length,
          steps_unplanned: gaps.length,
          total_cost_usd: Math.round(running * 1e6) / 1e6,
          weak_matches: steps.filter((s) => s.chosen.match_confidence === 'low').length,
          budget_usd: budget,
          budget_remaining_usd: Math.round((budget - running) * 1e6) / 1e6,
          within_budget: withinBudget,
          complete: gaps.length === 0 && steps.length > 0,
          complete_note: gaps.length === 0 && steps.length > 0
            ? 'Every step found a candidate above the relevance floor. That is not the same as the plan being correct — read match_confidence per step.'
            : 'Some steps could not be planned; see gaps[].',
        },
        gaps,
        execution: {
          performed: false,
          why_not:
            'This endpoint compiles plans; it does not spend. Executing would mean Animica holding a funded wallet and paying strangers on your behalf inside a model-driven loop, against endpoints neither of us wrote. You execute this with your own wallet, having read it first.',
          how_to_run:
            'Each step gives a resource, method and request shape. Call it with no payment to receive its 402, sign the payment it asks for, and retry with the X-PAYMENT header. Verify any step first with POST /x402/mesh/probe.',
        },
        pool: {
          indexed_services: index.records.length,
          callable_and_buyable: usable.length,
          probe_verified: index.counts.probe ? index.counts.probe.paywalled : 0,
          require_verified: requireVerified,
        },
        caveats: [
          `Planned from the ${usable.length} services that publish a callable request shape, out of ${index.records.length} indexed. The rest of the economy is not plannable yet — a URL with no request shape is not a plan item.`,
          'Prices marked as coming from a directory listing have not been confirmed by us. Ones marked as read from the merchant\'s own 402 were.',
          'DATAFLOW BETWEEN STEPS IS NOT VERIFIED. We have not run this plan, and most services publish no output schema, so we cannot prove step N accepts what step N-1 emits. Treat multi-step plans as a shortlist with an order, not a wired pipeline.',
          `Capability matching is lexical, with a ${Math.round(Number(cfg.solveMinCoverage) * 100)}% word-coverage floor: a candidate that does not clearly cover the capability is reported as a gap rather than planned. That means real services phrased differently get missed — but it also means a step you see was not chosen just for being the least-bad of 563.`,
          'Nothing here was purchased and no third party was paid. You paid Animica for the plan.',
        ],
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

module.exports = { createSolveProduct, DECOMPOSE_SCHEMA, shapeCandidate };
