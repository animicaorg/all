'use strict';
/**
 * Settlement engine for exact-EVM (EIP-3009). The invariant order is the
 * whole game:
 *
 *   1. full re-verify (stateless checks + chain reads)
 *   2. gas circuit breaker (BEFORE claiming — leave the authorization
 *      usable when we are the ones refusing)
 *   3. ATOMIC claim in the persistent store (UNIQUE authorization_hash;
 *      the DB serializes concurrent settles — exactly one claims)
 *   4. fees + gas estimate (estimateGas doubles as the authoritative
 *      simulation: consumed nonce, bad sig, paused token all revert here)
 *   5. sign the EIP-1559 tx, persist raw tx + hash + nonce (status
 *      'submitting') BEFORE broadcast — after this point the row can never
 *      be reclaimed and crash recovery owns any unknown outcome
 *   6. broadcast (never retried blindly), poll receipt, require status 1
 *      AND the AuthorizationUsed/Transfer logs AND N confirmations
 *   7. persist 'settled' + gas accounting | 'failed' + reason
 *
 * Unknown outcomes (send transport error, receipt timeout) stay
 * 'submitting' — never rebroadcast a DIFFERENT tx for the same
 * authorization; recoverInFlight() resolves them from chain truth
 * (authorizationState / getTransactionByHash / getLogs) on startup.
 *
 * The facilitator account's tx nonce is protected by a process-wide FIFO
 * mutex around steps 4-6a; volume at $0.01-a-call scale does not need
 * parallel nonce lanes, and serial submission makes nonce reasoning exact.
 *
 * The mutex alone is not enough when a SECOND writer (the treasury) signs
 * from the same account: `eth_getTransactionCount(...,'pending')` is answered
 * by whichever node the RPC front-end picked, and that node may not have seen
 * the transaction we broadcast a moment ago. Both writers therefore take
 * their nonce from ONE allocator (nonce.js) that keeps a fresh high-water
 * mark, inside the same lock. See its header for the failure it prevents.
 */

const evm = require('./evm');
const usdc = require('./usdc');
const gasMod = require('./gas');
const { VerifyReject, verifyPayment } = require('./verify');
const { createNonceAllocator } = require('./nonce');
const { newPaymentId } = require('../logging');

/**
 * `hooks` is a mutable object the caller may fill in AFTER construction
 * (the treasury needs this engine's submit lock, so it cannot exist yet when
 * the engine is built). The only hook today is onSettled, and it is called
 * fire-and-forget: never awaited, every throw swallowed. A settlement is
 * complete when its receipt is confirmed — nothing downstream of that may
 * delay the response or fail the payment.
 */
function createSettlementEngine({
  cfg, rpc, store, signer, metrics, logger, domainSepBytes, hooks = {},
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  now = Date.now,
  // One allocator per facilitator account. The treasury is handed THIS one
  // (facilitator-evm/server.js) so both writers share a single lane.
  nonces = createNonceAllocator({ rpc, address: signer.address, now, logger }),
}) {
  let submitLock = Promise.resolve();

  function fireSettled(info) {
    const fn = hooks && hooks.onSettled;
    if (typeof fn !== 'function') return;
    try {
      const p = fn(info);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (e) {
      logger.warn('settled_hook_failed', { detail: e.message });
    }
  }

  /** FIFO mutex: callers of fn are serialized in arrival order. */
  function withSubmitLock(fn) {
    const run = submitLock.then(fn, fn);
    submitLock = run.then(() => undefined, () => undefined);
    return run;
  }

  function fail(reason, { payer, transaction = '', detail } = {}) {
    if (metrics) metrics.settlementFailuresTotal.inc({ reason });
    const out = { success: false, errorReason: reason, transaction, network: cfg.caip2 };
    if (payer) out.payer = payer;
    if (detail) out.errorDetail = detail;
    return out;
  }

  function resourceOf(body) {
    const r = body && body.paymentPayload && body.paymentPayload.resource;
    if (typeof r === 'string') return r;
    if (r && typeof r.url === 'string') return r.url;
    return '';
  }

  async function waitForReceipt(txHash, budgetMs) {
    const deadline = now() + budgetMs;
    while (now() < deadline) {
      let receipt = null;
      try {
        receipt = await rpc.call('eth_getTransactionReceipt', [txHash]);
      } catch (e) { /* transient — keep polling until the budget runs out */ }
      if (receipt) return receipt;
      await sleep(cfg.receiptPollMs);
    }
    return null;
  }

  async function waitForConfirmations(receipt, budgetMs) {
    if (cfg.confirmations <= 0) return true;
    const target = evm.quantityToBigInt(receipt.blockNumber) + BigInt(cfg.confirmations);
    const deadline = now() + budgetMs;
    while (now() < deadline) {
      try {
        const latest = evm.quantityToBigInt(await rpc.call('eth_blockNumber', []));
        if (latest >= target) return true;
      } catch (e) { /* transient */ }
      await sleep(cfg.receiptPollMs);
    }
    return false;
  }

  /**
   * Poll + validate a broadcast settlement and persist the outcome.
   * Returns a SettlementResponse-shaped object.
   */
  async function confirmSettlement(paymentId, txHash, facts, budgetMs) {
    const receipt = await waitForReceipt(txHash, budgetMs);
    if (!receipt) {
      // Unknown outcome: stays 'submitting'; crash recovery / reconcile owns it.
      logger.warn('settlement_receipt_timeout', { payment_id: paymentId, settlement_tx: txHash });
      return fail('unexpected_settle_error', { payer: facts.payer, transaction: txHash, detail: 'no receipt within deadline; settlement state is recoverable, not final' });
    }
    const gasSpent = gasMod.receiptGasSpentWei(receipt);
    if (metrics && gasSpent > 0n) metrics.gasSpentWei.inc({}, gasSpent);
    if (receipt.status !== '0x1') {
      store.markFailed(paymentId, 'invalid_transaction_state', { gasSpentWei: gasSpent, txHash });
      logger.error('settlement_reverted', { payment_id: paymentId, settlement_tx: txHash, status: receipt.status });
      return fail('invalid_transaction_state', { payer: facts.payer, transaction: txHash });
    }
    const logCheck = usdc.validateSettlementReceipt(receipt, {
      token: cfg.asset,
      payer: facts.payer,
      payTo: cfg.settlementAddress,
      value: facts.authorization.value,
      nonce: facts.authorization.nonce,
    });
    if (!logCheck.ok) {
      // Status 1 without our exact effect should be impossible for calldata
      // we built ourselves — treat loudly as failed, keep gas accounted.
      store.markFailed(paymentId, `receipt_effect_mismatch: ${logCheck.reason}`, { gasSpentWei: gasSpent, txHash });
      logger.error('settlement_effect_mismatch', { payment_id: paymentId, settlement_tx: txHash, reason: logCheck.reason });
      return fail('unexpected_settle_error', { payer: facts.payer, transaction: txHash });
    }
    const confirmed = await waitForConfirmations(receipt, budgetMs);
    if (!confirmed) {
      logger.warn('settlement_confirmation_timeout', { payment_id: paymentId, settlement_tx: txHash });
      return fail('unexpected_settle_error', { payer: facts.payer, transaction: txHash, detail: 'confirmations pending; settlement state is recoverable, not final' });
    }

    store.markSettled(paymentId, { txHash, gasSpentWei: gasSpent });
    if (metrics) {
      metrics.settlementsTotal.inc({});
      metrics.revenueUsdc.inc({ product: facts.resource || 'unknown' }, facts.authorization.value);
    }
    logger.info('settlement_settled', {
      payment_id: paymentId,
      settlement_tx: txHash,
      payer: facts.payer,
      amount: facts.authorization.value.toString(),
      asset: cfg.asset,
      network: cfg.caip2,
      gas_spent_wei: gasSpent.toString(),
      status: 'settled',
    });
    // Post-settlement treasury trigger. Detached on purpose: the payer's
    // response must not wait on a balance read, let alone on a swap.
    fireSettled({ paymentId, txHash, payer: facts.payer, amount: facts.authorization.value, gasSpentWei: gasSpent });
    return {
      success: true,
      transaction: txHash,
      network: cfg.caip2,
      payer: facts.payer,
      amount: facts.authorization.value.toString(),
    };
  }

  async function settle(body) {
    const started = now();
    let facts;
    try {
      // skipStoreCheck: the atomic claim below is the single DB arbiter —
      // no verify-then-claim window, and settled rows answer idempotently.
      facts = await verifyPayment(body, { cfg, rpc, store, domainSepBytes, skipStoreCheck: true });
    } catch (e) {
      if (e instanceof VerifyReject) {
        if (e.reason === 'invalid_transaction_state' && metrics) metrics.replaysRejectedTotal.inc({});
        return fail(e.reason, { detail: e.message });
      }
      logger.error('settle_verify_crashed', { error: e.message });
      return fail('unexpected_settle_error');
    }
    facts.resource = resourceOf(body);

    // Circuit breaker BEFORE claiming: when we refuse, the payer's
    // authorization stays fully usable elsewhere/later.
    const budgetErr = gasMod.checkDailyBudget(store, cfg.dailyGasBudgetWei);
    if (budgetErr) {
      logger.error('gas_budget_open', { detail: budgetErr.message });
      return fail('unexpected_settle_error', { payer: facts.payer, detail: budgetErr.reason });
    }

    // ATOMIC claim — the replay gate. Exactly one concurrent settle wins.
    const paymentId = newPaymentId();
    const claim = store.claim({
      paymentId,
      authorizationHash: facts.digestHex,
      payer: facts.payer,
      asset: cfg.asset,
      network: cfg.caip2,
      amount: facts.authorization.value,
      resource: facts.resource,
      expiresAt: facts.authorization.validBefore,
      authNonce: facts.authorization.nonce,
    });
    if (!claim.claimed) {
      if (metrics) metrics.replaysRejectedTotal.inc({});
      const existing = claim.existing;
      if (existing && existing.status === 'settled') {
        // Idempotent re-settle of an already-settled payment: report the
        // stored truth instead of failing (same payment, no second transfer).
        return {
          success: true,
          transaction: existing.settlement_tx_hash || '',
          network: cfg.caip2,
          payer: existing.payer,
          amount: existing.amount,
        };
      }
      logger.warn('replay_rejected', { payer: facts.payer, authorization_hash: facts.digestHex, existing_status: existing && existing.status });
      return fail('invalid_transaction_state', { payer: facts.payer, detail: 'authorization already claimed for settlement' });
    }
    const activePaymentId = claim.paymentId;

    try {
      const result = await withSubmitLock(async () => {
        // Fees + gas (estimateGas == authoritative simulation).
        let fees, gasLimit, gasReservedWei = null;
        try {
          fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
          const est = await gasMod.estimateGasLimit(rpc, {
            from: signer.address,
            to: cfg.asset,
            data: usdc.transferAuthCalldata(facts.authorization, facts.signature),
            maxGasPerSettlement: cfg.maxGasPerSettlement,
          });
          gasLimit = est.gasLimit;

          // Authoritative budget re-check, INSIDE the lock and with the real
          // worst-case cost of this tx. The pre-claim check above is only an
          // early-out; concurrent settles all clear it against the same stale
          // recorded spend, so this is the one that actually bounds the day.
          const reserveWei = gasLimit * fees.maxFeePerGas;
          const lateBudgetErr = gasMod.checkDailyBudget(store, cfg.dailyGasBudgetWei, { reserveWei });
          if (lateBudgetErr) throw lateBudgetErr;

          // Refuse to sponsor gas worth more than the payment collects
          // (no-op unless the operator configured an ETH/USD price).
          const floorErr = gasMod.checkEconomicFloor({
            usdcAtomic: facts.authorization.value,
            reserveWei,
            ethUsdPrice: cfg.ethUsdPrice,
            safetyMultiple: cfg.gasSafetyMultiple,
          });
          if (floorErr) throw floorErr;
          gasReservedWei = reserveWei;
        } catch (e) {
          const reason = e instanceof gasMod.GasPolicyError
            ? (e.reason === 'simulation_reverted' ? 'invalid_transaction_state' : 'unexpected_settle_error')
            : 'unexpected_settle_error';
          store.markFailed(activePaymentId, `${e.reason || 'gas_error'}: ${e.message}`);
          logger.error('settlement_gas_rejected', { payment_id: activePaymentId, payer: facts.payer, detail: e.message });
          return fail(reason, { payer: facts.payer, detail: e.message });
        }

        // Facilitator account nonce (sequential tx nonce, NOT the 3009 nonce).
        // From the shared allocator: max(remote pending, our own high-water
        // mark), so a lagging RPC front-end can never hand this settlement a
        // nonce the treasury already broadcast on.
        let txNonce;
        try {
          const alloc = await nonces.next();
          txNonce = alloc.nonce;
          if (alloc.source === 'high_water') {
            logger.warn('nonce_rpc_lagging', {
              payment_id: activePaymentId,
              remote_pending: alloc.remotePending,
              using_nonce: txNonce,
              detail: 'the RPC pending count is behind our own last broadcast; using the local high-water mark',
            });
          }
        } catch (e) {
          store.markFailed(activePaymentId, `nonce_fetch_failed: ${e.message}`);
          return fail('unexpected_settle_error', { payer: facts.payer, detail: 'could not fetch facilitator nonce' });
        }

        const signed = signer.signTx({
          chainId: cfg.chainId,
          nonce: txNonce,
          maxPriorityFeePerGas: fees.maxPriorityFeePerGas,
          maxFeePerGas: fees.maxFeePerGas,
          gasLimit,
          to: cfg.asset,
          value: 0n,
          data: '0x' + evm.strip0x(usdc.transferAuthCalldata(facts.authorization, facts.signature)),
        });

        // Persist BEFORE broadcast: whatever happens next, recovery knows
        // exactly which signed bytes may be in flight. The nonce is claimed at
        // the same moment for the same reason.
        nonces.commit(txNonce);
        store.markSubmitting(activePaymentId, { txHash: signed.hash, txNonce, rawTx: signed.rawTx, gasReservedWei });
        logger.info('settlement_submitting', {
          payment_id: activePaymentId,
          settlement_tx: signed.hash,
          payer: facts.payer,
          amount: facts.authorization.value.toString(),
          tx_nonce: txNonce,
          max_fee_per_gas: fees.maxFeePerGas.toString(),
          gas_limit: gasLimit.toString(),
        });

        try {
          await rpc.call('eth_sendRawTransaction', [signed.rawTx]);
        } catch (e) {
          if (e && e.transport) {
            // Unknown outcome — the send may have landed. Fall through to
            // receipt polling; recovery owns it if polling times out too.
            logger.warn('settlement_send_unknown', { payment_id: activePaymentId, settlement_tx: signed.hash, detail: e.message });
          } else {
            // Definitive node-side rejection of a first-time send: nothing
            // is in flight. Failed, gas untouched, authorization unconsumed
            // on-chain (row keeps raw_tx, so it is not reclaimable — payer
            // signs a fresh authorization instead; safe beats convenient).
            // Hand the nonce back: nothing occupies it, and keeping the mark
            // advanced would sign the NEXT transaction into a permanent gap.
            nonces.release(txNonce);
            store.markFailed(activePaymentId, `send_rejected: ${e.message}`);
            logger.error('settlement_send_rejected', { payment_id: activePaymentId, settlement_tx: signed.hash, detail: e.message });
            return fail('unexpected_settle_error', { payer: facts.payer, detail: 'transaction rejected by rpc node' });
          }
        }

        return confirmSettlement(activePaymentId, signed.hash, facts, cfg.receiptTimeoutMs);
      });
      return result;
    } finally {
      if (metrics) metrics.paymentLatencySeconds.observe({}, (now() - started) / 1000);
    }
  }

  /**
   * Crash recovery — run at startup BEFORE serving /settle. For every row
   * whose outcome is unknown, chain truth decides:
   *
   *   'pending' rows (claimed, never signed): fail them (raw_tx stays NULL,
   *     so the same authorization can be re-claimed by a retry).
   *   'submitting' rows: authorizationState(payer, nonce) is the ledger —
   *     consumed => find the tx (stored hash, else AuthorizationUsed logs),
   *     mark settled; unconsumed + our tx vanished => rebroadcast the SAME
   *     signed bytes (never a new tx); unconsumed + tx still pending =>
   *     leave 'submitting' for the next pass.
   */
  async function recoverInFlight() {
    const report = { settled: 0, rebroadcast: 0, failedPending: 0, stillUnknown: 0 };

    for (const row of store.listByStatus('pending', 1000)) {
      store.markFailed(row.payment_id, 'crash_before_submission');
      report.failedPending++;
      logger.warn('recovery_pending_failed', { payment_id: row.payment_id, payer: row.payer });
    }

    for (const row of store.listSubmitting()) {
      try {
        const consumed = BigInt(await rpc.call('eth_call', [
          { to: cfg.asset, data: usdc.authorizationStateCalldata(row.payer, row.auth_nonce) },
          'latest',
        ])) !== 0n;

        if (consumed) {
          let txHash = row.settlement_tx_hash;
          let gasSpent = null;
          let receipt = txHash ? await rpc.call('eth_getTransactionReceipt', [txHash]) : null;
          if (!receipt) {
            // Authorization landed but not via a receipt we can see under
            // our stored hash — locate it via the AuthorizationUsed event.
            const found = await findAuthorizationUsedTx(row);
            if (found) { txHash = found; receipt = await rpc.call('eth_getTransactionReceipt', [txHash]); }
          }
          if (receipt && receipt.status === '0x1') {
            gasSpent = gasMod.receiptGasSpentWei(receipt);
            if (metrics && gasSpent > 0n) metrics.gasSpentWei.inc({}, gasSpent);
          }
          store.markSettled(row.payment_id, { txHash: txHash || null, gasSpentWei: gasSpent });
          if (metrics) {
            metrics.settlementsTotal.inc({});
            metrics.revenueUsdc.inc({ product: row.resource || 'unknown' }, BigInt(row.amount));
          }
          report.settled++;
          logger.info('recovery_settled', { payment_id: row.payment_id, settlement_tx: txHash, payer: row.payer, status: 'settled' });
          continue;
        }

        // Not consumed. Where is our tx?
        const pendingTx = await rpc.call('eth_getTransactionByHash', [row.settlement_tx_hash]);
        if (pendingTx === null) {
          if (Math.floor(Date.now() / 1000) >= row.expires_at) {
            // Authorization expired on-chain; the tx would revert anyway.
            store.markExpired(row.payment_id, 'authorization expired before settlement landed');
            report.stillUnknown++;
            continue;
          }
          // Dropped from the mempool: rebroadcasting the SAME signed bytes
          // is always safe (same nonce, same authorization).
          await rpc.call('eth_sendRawTransaction', [row.raw_tx]);
          // Claim its nonce in the shared lane so the first settlement after
          // recovery does not sign a duplicate while the RPC catches up.
          if (row.tx_nonce !== null && row.tx_nonce !== undefined) nonces.commit(Number(row.tx_nonce));
          report.rebroadcast++;
          logger.warn('recovery_rebroadcast', { payment_id: row.payment_id, settlement_tx: row.settlement_tx_hash });
        } else {
          report.stillUnknown++;
        }
      } catch (e) {
        report.stillUnknown++;
        logger.error('recovery_error', { payment_id: row.payment_id, detail: e.message });
      }
    }
    return report;
  }

  async function findAuthorizationUsedTx(row) {
    try {
      const latest = evm.quantityToBigInt(await rpc.call('eth_blockNumber', []));
      const from = latest > 100000n ? latest - 100000n : 0n;
      const logs = await rpc.call('eth_getLogs', [{
        address: cfg.asset,
        fromBlock: '0x' + from.toString(16),
        toBlock: 'latest',
        topics: [
          usdc.TOPICS.authorizationUsed,
          '0x' + '0'.repeat(24) + evm.strip0x(row.payer).toLowerCase(),
          row.auth_nonce.toLowerCase(),
        ],
      }]);
      if (Array.isArray(logs) && logs.length > 0) return logs[0].transactionHash;
    } catch (e) {
      logger.warn('recovery_getlogs_failed', { payment_id: row.payment_id, detail: e.message });
    }
    return null;
  }

  return { settle, recoverInFlight, confirmSettlement, withSubmitLock, nonces };
}

module.exports = { createSettlementEngine };
