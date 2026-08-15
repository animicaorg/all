'use strict';
/**
 * Bounded EVM JSON-RPC client. Policy per the recon on mainnet.base.org
 * (public endpoint rate-limits with -32016 on bursts): small requests, a hard
 * per-call timeout, a small bounded retry with backoff for TRANSPORT errors
 * only. JSON-RPC -32xxx *application* errors are surfaced immediately —
 * retrying an execution revert just burns rate limit; retrying a send can
 * only be decided by the settlement layer, which owns the tx state machine.
 *
 * eth_sendRawTransaction is NEVER retried here for that reason: after one
 * attempt the outcome is unknown-at-worst and the caller must go through the
 * crash-recovery path (authorizationState / getTransactionByHash), not a
 * blind resend loop.
 */

class RpcError extends Error {
  constructor(message, { code, data, transport = false } = {}) {
    super(message);
    this.code = code;
    this.data = data;
    this.transport = transport; // true = never reached/parsed the server
  }
}

function createRpcClient(url, {
  fetchImpl = fetch,
  timeoutMs = 10_000,
  retries = 2,
  retryDelayMs = 250,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
} = {}) {
  if (!/^https?:\/\//.test(String(url))) throw new Error(`rpc url must be http(s), got ${JSON.stringify(url)}`);
  let idCounter = 1;

  async function callOnce(method, params) {
    let res;
    try {
      res = await fetchImpl(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: idCounter++, method, params }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (e) {
      throw new RpcError(`rpc transport error: ${e.message}`, { transport: true });
    }
    if (!res.ok) throw new RpcError(`rpc http ${res.status}`, { transport: true });
    let body;
    try {
      body = await res.json();
    } catch (e) {
      throw new RpcError('rpc returned non-json', { transport: true });
    }
    if (body.error) {
      throw new RpcError(body.error.message || 'rpc error', { code: body.error.code, data: body.error.data });
    }
    return body.result;
  }

  return {
    url,
    async call(method, params = []) {
      const maxAttempts = method === 'eth_sendRawTransaction' ? 1 : 1 + retries;
      let lastErr;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        try {
          return await callOnce(method, params);
        } catch (e) {
          lastErr = e;
          const retryable = e instanceof RpcError && (e.transport || e.code === -32016 /* rate limit */);
          if (!retryable || attempt === maxAttempts - 1) throw e;
          await sleep(retryDelayMs * (attempt + 1));
        }
      }
      throw lastErr;
    },
  };
}

module.exports = { createRpcClient, RpcError };
