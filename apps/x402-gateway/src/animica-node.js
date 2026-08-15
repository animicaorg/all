'use strict';
/**
 * JSON-RPC 2.0 client for the LOCAL Animica node (default
 * http://127.0.0.1:8545/rpc — never the public vhosts; the paid products
 * exist so the public surfaces DON'T get hammered).
 *
 * Two hard-won rules from the chain-data recon are enforced here, not left
 * to callers:
 *
 *  1. BigInt hazard: in-block tx `value`/`tip` arrive as JSON *numbers* and
 *     live balances already exceed 2^53 — JSON.parse would silently corrupt
 *     them. The raw response text is rewritten to quote those two fields
 *     before parsing, so they surface as exact decimal STRINGS end-to-end.
 *     (The rewrite matches the literal key-colon-digits sequence; no
 *     Animica RPC string field can contain `"value":<digits>` — string
 *     values on this API are hex/bech32/ids.)
 *
 *  2. Single flight: the node serializes ALL RPC work on one event loop
 *     shared with miner getwork and wallets (this box has prior RPC CPU
 *     starvation incidents). Every request through this client — single or
 *     batch — is serialized behind one in-flight slot; parallelism was
 *     measured to add only contention.
 */

const BIG_MONEY_FIELDS = /"(value|tip)"\s*:\s*(-?\d+)/g;

class NodeRpcError extends Error {
  constructor(message, { code, method } = {}) {
    super(message);
    this.name = 'NodeRpcError';
    this.code = code;
    this.method = method;
  }
}

function parseRpcText(text) {
  return JSON.parse(text.replace(BIG_MONEY_FIELDS, '"$1":"$2"'));
}

function createNodeClient(url, { fetchImpl = fetch, timeoutMs = 5000 } = {}) {
  let inFlight = Promise.resolve();

  /** Serialize `fn` behind everything already queued (single-flight). */
  function enqueue(fn) {
    const run = inFlight.then(fn, fn);
    // Keep the chain alive whether fn resolved or rejected.
    inFlight = run.then(() => undefined, () => undefined);
    return run;
  }

  async function post(payload, budgetMs) {
    const res = await fetchImpl(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(budgetMs || timeoutMs),
    });
    if (!res.ok) throw new NodeRpcError(`node rpc http ${res.status}`, {});
    return parseRpcText(await res.text());
  }

  return {
    url,

    /** Single call; throws NodeRpcError on JSON-RPC error / transport. */
    call(method, params = {}, { timeoutMs: budget } = {}) {
      return enqueue(async () => {
        const out = await post({ jsonrpc: '2.0', id: 1, method, params }, budget);
        if (out && out.error) {
          throw new NodeRpcError(out.error.message || 'rpc error', { code: out.error.code, method });
        }
        return out ? out.result : undefined;
      });
    },

    /**
     * Batched call: [{method, params}, ...] -> results aligned by id.
     * A per-item JSON-RPC error surfaces as NodeRpcError for the whole
     * batch (bulk export treats any failed block as its truncation point,
     * never a hole).
     */
    batch(calls, { timeoutMs: budget } = {}) {
      return enqueue(async () => {
        if (!Array.isArray(calls) || calls.length === 0) return [];
        const payload = calls.map((c, i) => ({ jsonrpc: '2.0', id: i, method: c.method, params: c.params || {} }));
        const out = await post(payload, budget);
        if (!Array.isArray(out)) {
          if (out && out.error) throw new NodeRpcError(out.error.message || 'rpc error', { code: out.error.code });
          throw new NodeRpcError('node rpc: batch response is not an array', {});
        }
        const byId = new Map(out.map((r) => [r.id, r]));
        return calls.map((c, i) => {
          const r = byId.get(i);
          if (!r) throw new NodeRpcError(`node rpc: batch response missing id ${i}`, { method: c.method });
          if (r.error) throw new NodeRpcError(r.error.message || 'rpc error', { code: r.error.code, method: c.method });
          return r.result;
        });
      });
    },
  };
}

module.exports = { createNodeClient, NodeRpcError, parseRpcText };
