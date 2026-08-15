#!/usr/bin/env node
'use strict';
/**
 * Self-hosted exact-EVM facilitator server (x402 v2 §7). Binds loopback
 * (default 127.0.0.1:8743) and speaks only the protocol-required routes
 * plus health/ops:
 *
 *   POST /verify    {x402Version:2, paymentPayload, paymentRequirements}
 *                   -> {isValid, payer} | {isValid:false, invalidReason}
 *   POST /settle    same body -> {success, transaction, network, payer,
 *                   amount} | {success:false, errorReason, transaction:""}
 *   GET  /supported -> {kinds, extensions, signers}
 *   GET  /healthz   -> process liveness (no dependencies touched)
 *   GET  /readyz    -> FAILS when: RPC down | chain-id mismatch | USDC
 *                   domain-separator mismatch (live read vs local compute)
 *                   | DB down | gas balance < X402_MIN_GAS_BALANCE_WEI.
 *                   Key validity is enforced harder than a readyz: a bad
 *                   key refuses startup entirely.
 *   GET  /metrics   -> Prometheus text (loopback only, like everything here)
 *
 * Startup order: config (fail closed) -> key (fail closed) -> store (WAL)
 * -> LIVE domain-separator self-check -> crash recovery of in-flight
 * settlements -> listen. Recovery runs BEFORE the first /settle can race
 * with it.
 */

const http = require('node:http');

const cfgMod = require('../config');
const evm = require('./evm');
const usdc = require('./usdc');
const { createRpcClient } = require('./rpc');
const { loadSigner } = require('./key');
const { createStore } = require('../store');
const { createMetrics } = require('../metrics');
const { createLogger, newRequestId } = require('../logging');
const { VerifyReject, verifyPayment } = require('./verify');
const { createSettlementEngine } = require('./settlement');
const { createTreasury, createTreasuryStore, assertTreasuryPolicy } = require('../treasury');

const MAX_BODY_BYTES = 64 * 1024; // an eip3009 payload is ~1 KB; 64K is generous
const REQUEST_TIMEOUT_MS = 90_000; // > settle receipt budget, < forever

function readJson(req, maxBytes = MAX_BODY_BYTES) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooBig = false;
    req.on('data', (c) => {
      if (tooBig) return; // discard the rest so the 413 can be delivered cleanly
      size += c.length;
      if (size > maxBytes) {
        tooBig = true;
        chunks.length = 0;
        if (size > maxBytes * 8) req.destroy(); // hard stop for hostile streams
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (tooBig) return reject(new Error('body too large'));
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
      } catch (e) {
        reject(new Error('invalid json'));
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
}

/**
 * Build the facilitator from resolved pieces. Everything is injectable so
 * tests run it against a mock RPC and an in-memory DB with throwaway keys.
 */
function createEvmFacilitator({ cfg, rpc, store, signer, metrics, logger, sleep, now }) {
  metrics = metrics || createMetrics();
  logger = logger || createLogger({ service: 'x402-facilitator-evm' });

  // EIP-712 domain from allowlisted config; self-consistency is enforced at
  // startup (checkDomainSeparator) and continuously via /readyz.
  const domainSepBytes = evm.domainSeparator({
    name: cfg.eip712.name,
    version: cfg.eip712.version,
    chainId: cfg.chainId,
    verifyingContract: cfg.asset,
  });
  const localDomainSep = evm.bytesToHex(domainSepBytes);
  if (localDomainSep.toLowerCase() !== cfg.expectedDomainSeparator.toLowerCase()) {
    // Allowlist table and our own EIP-712 code disagree — never serve.
    throw new Error(
      `locally computed domain separator ${localDomainSep} != allowlisted ${cfg.expectedDomainSeparator} for ${cfg.network}`
    );
  }

  // Single-wallet mode (payTo == our own address) is refused unless the
  // treasury is enabled with a cold address to drain into. Enforced here
  // because this is the first place the derived facilitator address exists.
  const walletPolicy = assertTreasuryPolicy(cfg, signer.address);

  const hooks = {};
  const engine = createSettlementEngine({ cfg, rpc, store, signer, metrics, logger, domainSepBytes, hooks, sleep, now });

  /**
   * Treasury. It shares the settlement engine's FIFO submit lock AND its
   * nonce allocator, so the facilitator's tx nonce stays coherent even when a
   * load-balanced RPC answers `eth_getTransactionCount(...,'pending')` from a
   * node that has not seen our last broadcast. It holds the lock only for a
   * single sign+broadcast — never across receipt polling — so a settlement
   * arriving mid-sip queues behind one RPC round trip and nothing more.
   */
  let treasury = null;
  if (cfg.treasury && cfg.treasury.enabled) {
    const tstore = createTreasuryStore(store.db, { now });
    treasury = createTreasury({
      cfg, rpc, tstore, signer, metrics, logger,
      withSubmitLock: engine.withSubmitLock,
      nonces: engine.nonces,
      now, sleep,
    });
    hooks.onSettled = () => treasury.notifySettlement();
  }

  /** Live-vs-local domain separator check. One eth_call catches every
   * mis-config class: wrong token, wrong chain, wrong name/version. */
  async function checkDomainSeparator() {
    const live = await rpc.call('eth_call', [{ to: cfg.asset, data: usdc.SELECTORS.domainSeparator }, 'latest']);
    if (typeof live !== 'string' || live.toLowerCase() !== localDomainSep.toLowerCase()) {
      throw new Error(`live DOMAIN_SEPARATOR() ${live} != locally computed ${localDomainSep}`);
    }
    return true;
  }

  async function readiness() {
    const checks = {};
    let ready = true;
    // RPC + chain id
    try {
      const chainId = evm.quantityToBigInt(await rpc.call('eth_chainId', []));
      checks.rpc = true;
      checks.chain_id = chainId === BigInt(cfg.chainId) || `mismatch: got ${chainId}, want ${cfg.chainId}`;
      if (checks.chain_id !== true) ready = false;
    } catch (e) {
      checks.rpc = `unreachable: ${e.message}`;
      checks.chain_id = 'unknown';
      ready = false;
    }
    // USDC config (live domain separator)
    if (checks.rpc === true) {
      try {
        await checkDomainSeparator();
        checks.usdc_domain = true;
      } catch (e) {
        checks.usdc_domain = e.message;
        ready = false;
      }
    } else {
      checks.usdc_domain = 'skipped: rpc down';
      ready = false;
    }
    // DB
    try {
      store.ping();
      checks.db = true;
    } catch (e) {
      checks.db = `down: ${e.message}`;
      ready = false;
    }
    // Gas balance floor
    if (checks.rpc === true) {
      try {
        const bal = evm.quantityToBigInt(await rpc.call('eth_getBalance', [signer.address, 'latest']));
        metrics.gasBalanceWei.set({}, Number(bal)); // approximate gauge; exact value lives in logs
        checks.gas_balance =
          bal >= cfg.minGasBalanceWei || `low: ${bal} wei < X402_MIN_GAS_BALANCE_WEI ${cfg.minGasBalanceWei}`;
        if (checks.gas_balance !== true) ready = false;
      } catch (e) {
        checks.gas_balance = `unknown: ${e.message}`;
        ready = false;
      }
    } else {
      checks.gas_balance = 'skipped: rpc down';
      ready = false;
    }
    // Treasury health is a WARNING, never a readiness failure. Sipping is a
    // convenience that keeps the wallet fuelled; when it breaks the correct
    // response is "operator tops up ETH manually", not "stop taking payments
    // that are still perfectly settleable".
    const warnings = [];
    if (treasury) {
      const w = treasury.warning();
      if (w) {
        checks.treasury = `WARNING (not fatal): ${w}`;
        warnings.push(`treasury: ${w}`);
      } else {
        checks.treasury = true;
      }
    }
    metrics.ready.set({}, ready ? 1 : 0);
    return warnings.length ? { ready, checks, warnings } : { ready, checks };
  }

  async function verify(body) {
    try {
      const facts = await verifyPayment(body, { cfg, rpc, store, domainSepBytes });
      metrics.verificationsTotal.inc({ outcome: 'valid' });
      return { isValid: true, payer: facts.payer };
    } catch (e) {
      if (e instanceof VerifyReject) {
        metrics.verificationsTotal.inc({ outcome: e.reason });
        return { isValid: false, invalidReason: e.reason, invalidDetail: e.message };
      }
      logger.error('verify_crashed', { error: e.message });
      metrics.verificationsTotal.inc({ outcome: 'unexpected_verify_error' });
      return { isValid: false, invalidReason: 'unexpected_verify_error' };
    }
  }

  function supported() {
    return {
      kinds: [{ x402Version: 2, scheme: 'exact', network: cfg.caip2 }],
      extensions: [],
      signers: { 'eip155:*': [signer.address] },
    };
  }

  return {
    cfg,
    signer,
    metrics,
    logger,
    engine,
    treasury,
    singleWallet: walletPolicy.singleWallet,
    domainSepBytes,
    verify,
    settle: (body) => engine.settle(body),
    supported,
    readiness,
    checkDomainSeparator,
    recoverInFlight: () => engine.recoverInFlight(),
  };
}

function createEvmFacilitatorServer(facilitator) {
  const { logger, metrics } = facilitator;
  const server = http.createServer(async (req, res) => {
    const requestId = req.headers['x-request-id'] || newRequestId();
    const started = Date.now();
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('x-request-id', String(requestId));
    try {
      if (req.method === 'GET' && url.pathname === '/supported') {
        return sendJson(res, 200, facilitator.supported());
      }
      if (req.method === 'GET' && url.pathname === '/healthz') {
        return sendJson(res, 200, { ok: true, role: 'x402-facilitator-evm', network: facilitator.cfg.caip2 });
      }
      if (req.method === 'GET' && url.pathname === '/readyz') {
        const r = await facilitator.readiness();
        return sendJson(res, r.ready ? 200 : 503, r);
      }
      if (req.method === 'GET' && url.pathname === '/metrics') {
        const body = metrics.render();
        res.writeHead(200, { 'content-type': 'text/plain; version=0.0.4', 'content-length': Buffer.byteLength(body) });
        return res.end(body);
      }
      if (req.method === 'POST' && (url.pathname === '/verify' || url.pathname === '/settle')) {
        let body;
        try {
          body = await readJson(req);
        } catch (e) {
          return sendJson(res, e.message === 'body too large' ? 413 : 400, { error: e.message });
        }
        const out = url.pathname === '/verify'
          ? await facilitator.verify(body)
          : await facilitator.settle(body);
        logger.info(url.pathname === '/verify' ? 'verify' : 'settle', {
          request_id: requestId,
          payer: out.payer,
          status: url.pathname === '/verify' ? String(out.isValid) : (out.success ? 'settled' : out.errorReason),
          settlement_tx: out.transaction || undefined,
          amount: out.amount,
          network: facilitator.cfg.caip2,
          latency_ms: Date.now() - started,
        });
        return sendJson(res, 200, out);
      }
      return sendJson(res, 404, { error: 'not_found' });
    } catch (e) {
      logger.error('request_failed', { request_id: requestId, path: url.pathname, error: e.message });
      if (!res.headersSent) return sendJson(res, 500, { error: 'internal_error' });
      res.end();
    }
  });
  server.requestTimeout = REQUEST_TIMEOUT_MS;
  server.headersTimeout = 15_000;
  return server;
}

/** Compose from env — the production entrypoint. */
function buildFromEnv() {
  const cfg = cfgMod.loadEvmFacilitatorConfig(process.env);
  const signer = loadSigner(cfg.privateKey); // throws on malformed key
  delete cfg.privateKey; // the signer closure is the only holder from here on
  const logger = createLogger({ service: 'x402-facilitator-evm' });
  const rpc = createRpcClient(cfg.rpcUrl, { timeoutMs: cfg.rpcTimeoutMs, retries: cfg.rpcRetries });
  const store = createStore(cfg.dbPath);
  const facilitator = createEvmFacilitator({ cfg, rpc, store, signer, logger });
  return { cfg, facilitator, logger };
}

async function main() {
  if (process.env.ANM_X402_ENABLED !== '1') {
    console.error('refusing to start: set ANM_X402_ENABLED=1');
    process.exit(1);
  }
  let built;
  try {
    built = buildFromEnv();
  } catch (e) {
    // Config/key errors must never hot-loop with secrets in argv — print the
    // reason (never the values) and exit non-zero.
    console.error(`[x402-facilitator-evm] refusing to start: ${e.message}`);
    process.exit(1);
  }
  const { cfg, facilitator, logger } = built;

  logger.info('starting', {
    network: cfg.network,
    chain_id: cfg.chainId,
    asset: cfg.asset,
    settlement_address: cfg.settlementAddress,
    facilitator_address: facilitator.signer.address, // derived address ONLY — never the key
    db_path: cfg.dbPath,
    confirmations: cfg.confirmations,
    single_wallet: facilitator.singleWallet,
    treasury: facilitator.treasury
      ? { enabled: true, cold_address: facilitator.treasury.coldAddress }
      : { enabled: false },
  });

  try {
    await facilitator.checkDomainSeparator();
    logger.info('domain_separator_verified', { asset: cfg.asset });
  } catch (e) {
    logger.error('domain_separator_mismatch', { error: e.message });
    process.exit(1);
  }

  const recovery = await facilitator.recoverInFlight();
  logger.info('crash_recovery_done', recovery);

  // Treasury last: recovery must own the nonce lane until it is done, and a
  // sip fired mid-recovery would race the rebroadcast of a stored raw tx.
  // Its own in-flight actions are resolved from chain truth first — an
  // unresolved treasury transaction sits in front of every settlement in the
  // same nonce lane, so it is a settlement outage until it clears.
  if (facilitator.treasury) {
    try {
      const tRecovery = await facilitator.treasury.recover({ trigger: 'startup' });
      logger.info('treasury_recovery_done', tRecovery);
    } catch (e) {
      logger.error('treasury_recovery_failed', { detail: e.message });
    }
    facilitator.treasury.start();
  }

  const server = createEvmFacilitatorServer(facilitator);
  server.listen(cfg.port, cfg.bind, () => {
    logger.info('listening', { bind: cfg.bind, port: cfg.port });
  });
}

if (require.main === module) {
  main().catch((e) => {
    console.error(`[x402-facilitator-evm] fatal: ${e.message}`);
    process.exit(1);
  });
}

module.exports = { createEvmFacilitator, createEvmFacilitatorServer, buildFromEnv, readJson, sendJson, MAX_BODY_BYTES };
