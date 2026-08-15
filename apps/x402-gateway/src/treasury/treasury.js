'use strict';
/**
 * Treasury: "sweep and sip" for the single-wallet facilitator.
 *
 * The operator runs the facilitator with payTo == the facilitator's own
 * address, so USDC revenue lands on the same hot wallet that pays gas. This
 * module keeps that wallet self-refuelling and nearly empty:
 *
 *   SIP   ETH below X402_TREASURY_ETH_FLOOR_WEI -> swap a small amount of the
 *         accrued USDC to ETH on Uniswap v3 (SwapRouter02 exactInputSingle
 *         USDC->WETH + unwrapWETH9, one atomic multicall), sized
 *         min(SIP_USDC, available) but never below SIP_MIN_USDC. Adaptive
 *         sizing is the whole point: a fixed $5 floor deadlocks after a gas
 *         spike (ETH exhausted at ~75 settlements with ~$0.75 accrued), and a
 *         $0.50 sip already buys ~490 settlements of gas.
 *
 *   SWEEP USDC above X402_TREASURY_USDC_CEILING -> ERC-20 transfer the
 *         surplus to the cold address. The hot wallet is left holding
 *         operating float, not a balance sheet.
 *
 * Non-negotiables encoded here:
 *
 *   - Settlements are never blocked or delayed by treasury work. The module
 *     runs on its own timer and on a COALESCED post-settlement trigger that
 *     the settlement path fires and forgets. The only shared resource is the
 *     facilitator account's tx nonce, and the treasury holds the settlement
 *     engine's submit lock for exactly one sign+broadcast — never across
 *     receipt polling.
 *   - The sweep destination is captured at construction from server config
 *     and is otherwise unreachable: no argument, no request field, no later
 *     config mutation can retarget it.
 *   - Two consecutive swap FAILURES disable sipping and raise a /readyz
 *     WARNING. Paid traffic keeps settling while ETH lasts; the failure mode
 *     is "operator tops up manually", never "block payments" and never "loop
 *     swaps".
 *   - Every amount is a BigInt atomic unit (USDC 6 decimals, ETH wei). No
 *     float touches money, and a quote never round-trips through Number.
 */

const evm = require('../facilitator-evm/evm');
const gasMod = require('../facilitator-evm/gas');
const uniswap = require('./uniswap');
const { atomicToDecimalString } = require('../metrics');

const SEC = 1000;
const USDC_DECIMALS = 6;
const DAY_SECONDS = 86_400;

/** Outcome of an attempt that never ran (not a failure — nothing was spent). */
function skipped(reason, extra = {}) {
  return Object.assign({ action: 'skipped', reason }, extra);
}

function createTreasury({
  cfg,
  rpc,
  tstore,
  signer,
  metrics,
  logger,
  // The settlement engine's FIFO submit lock. Sharing it is what keeps the
  // facilitator's tx nonce coherent across settlements and treasury txs.
  // Default is a no-op so unit tests can drive the module standalone.
  withSubmitLock = (fn) => Promise.resolve().then(fn),
  now = Date.now,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
}) {
  const t = cfg.treasury;
  if (!t) throw new Error('createTreasury: cfg.treasury is missing (config was not loaded fail-closed)');

  const contracts = uniswap.contractsFor(cfg.network);

  /* ---------------------------------------------------------- immutables --
   * Everything money can move to is frozen here, at construction, from
   * server config. Later mutation of cfg (or of the environment) cannot
   * reach these bindings — that is the sweep-destination invariant, and the
   * tests assert it by mutating cfg after construction.
   */
  const ME = evm.validateAddress(signer.address, 'facilitator address');
  const USDC = evm.validateAddress(cfg.asset, 'X402_ASSET');
  const COLD = t.coldAddress ? evm.validateAddress(t.coldAddress, 'X402_TREASURY_COLD_ADDRESS') : null;
  const ROUTER = contracts.swapRouter02;
  const QUOTER = contracts.quoterV2;
  const WETH9 = contracts.weth9;

  if (!evm.addressEquals(USDC, contracts.usdc)) {
    throw new Error(
      `treasury: configured asset ${USDC} is not the ${cfg.network} USDC ${contracts.usdc} the verified pool set describes`
    );
  }
  if (t.enabled) {
    if (!COLD) throw new Error('treasury: X402_TREASURY_ENABLED=1 requires X402_TREASURY_COLD_ADDRESS');
    if (evm.addressEquals(COLD, ME)) {
      throw new Error('treasury: X402_TREASURY_COLD_ADDRESS equals the facilitator address — a sweep to self burns gas and drains nothing');
    }
    for (const [what, addr] of [['USDC', USDC], ['SwapRouter02', ROUTER], ['QuoterV2', QUOTER], ['WETH9', WETH9]]) {
      if (evm.addressEquals(COLD, addr)) {
        throw new Error(`treasury: X402_TREASURY_COLD_ADDRESS points at the ${what} contract ${addr} — swept USDC would be unrecoverable`);
      }
    }
  }

  /* -------------------------------------------------------------- state -- */

  let sippingDisabled = tstore.getState('sipping_disabled', '0') === '1';
  let sippingDisabledReason = tstore.getState('sipping_disabled_reason', null);
  let sipFailures = Number(tstore.getState('sip_consecutive_failures', '0')) || 0;
  let sweepingDisabled = tstore.getState('sweeping_disabled', '0') === '1';
  let sweepingDisabledReason = tstore.getState('sweeping_disabled_reason', null);
  let sweepFailures = Number(tstore.getState('sweep_consecutive_failures', '0')) || 0;

  let lastBalances = { ethWei: null, usdcAtomic: null, at: 0 };
  let lastTick = null;
  let timer = null;
  let inFlight = null;
  let queuedTrigger = null;

  const nowSec = () => Math.floor(now() / 1000);
  const usd = (atomic) => atomicToDecimalString(atomic, USDC_DECIMALS);

  // Publish the breaker gauges immediately: an unset gauge renders as 0,
  // which would read as "sipping disabled" on a perfectly healthy process.
  if (metrics) {
    metrics.treasurySippingEnabled.set({}, sippingDisabled ? 0 : 1);
    metrics.treasurySweepingEnabled.set({}, sweepingDisabled ? 0 : 1);
  }

  function persistSipState() {
    tstore.setState('sipping_disabled', sippingDisabled ? '1' : '0');
    tstore.setState('sipping_disabled_reason', sippingDisabledReason || '');
    tstore.setState('sip_consecutive_failures', String(sipFailures));
  }

  function persistSweepState() {
    tstore.setState('sweeping_disabled', sweepingDisabled ? '1' : '0');
    tstore.setState('sweeping_disabled_reason', sweepingDisabledReason || '');
    tstore.setState('sweep_consecutive_failures', String(sweepFailures));
  }

  /**
   * The failure policy. `hardDisable` short-circuits the two-strike rule for
   * classes that can only be a misconfiguration (STE: our own ETH recipient
   * rejects ETH) — spending two more reverting transactions to confirm a
   * permanent condition just burns gas.
   */
  function recordFailure(kind, reason, { hardDisable = false, strike = true } = {}) {
    if (kind === 'sip') {
      if (strike) sipFailures += 1;
      if (hardDisable || sipFailures >= t.maxConsecutiveFailures) {
        sippingDisabled = true;
        sippingDisabledReason = hardDisable
          ? `hard-disabled after a ${reason} failure (misconfiguration, not market noise)`
          : `disabled after ${sipFailures} consecutive swap failures (last: ${reason})`;
        logger.error('treasury_sipping_disabled', {
          reason: sippingDisabledReason,
          consecutive_failures: sipFailures,
          remedy: 'top the facilitator wallet up with ETH manually, fix the cause, then `animica-x402 treasury resume --confirm`',
        });
        if (metrics) metrics.treasurySippingEnabled.set({}, 0);
      }
      persistSipState();
      return;
    }
    if (strike) sweepFailures += 1;
    if (hardDisable || sweepFailures >= t.maxConsecutiveFailures) {
      sweepingDisabled = true;
      sweepingDisabledReason = `disabled after ${sweepFailures} consecutive sweep failures (last: ${reason})`;
      logger.error('treasury_sweeping_disabled', { reason: sweepingDisabledReason, consecutive_failures: sweepFailures });
      if (metrics) metrics.treasurySweepingEnabled.set({}, 0);
    }
    persistSweepState();
  }

  function recordSuccess(kind) {
    if (kind === 'sip') {
      if (sipFailures !== 0) { sipFailures = 0; persistSipState(); }
      return;
    }
    if (sweepFailures !== 0) { sweepFailures = 0; persistSweepState(); }
  }

  /* ---------------------------------------------------------- chain I/O -- */

  async function readBalances() {
    const ethWei = evm.quantityToBigInt(await rpc.call('eth_getBalance', [ME, 'latest']));
    const usdcAtomic = uniswap.decodeUint(
      await rpc.call('eth_call', [{ to: USDC, data: uniswap.balanceOfCalldata(ME) }, 'latest'])
    );
    lastBalances = { ethWei, usdcAtomic, at: now() };
    if (metrics) {
      metrics.treasuryEthBalanceWei.set({}, ethWei);
      metrics.treasuryUsdcBalance.set({}, usdcAtomic);
    }
    return lastBalances;
  }

  /**
   * USDC's own kill switches. Both are outside our control and both turn
   * every sip AND every sweep into a revert, so they are a skip (no strike),
   * not a failure.
   */
  async function tokenHealth() {
    const paused = uniswap.decodeBool(await rpc.call('eth_call', [{ to: USDC, data: uniswap.pausedCalldata() }, 'latest']));
    const blacklisted = uniswap.decodeBool(
      await rpc.call('eth_call', [{ to: USDC, data: uniswap.isBlacklistedCalldata(ME) }, 'latest'])
    );
    return { paused, blacklisted };
  }

  /** Quote every allowlisted fee tier and keep the best fill. Read-only. */
  async function bestQuote(amountIn) {
    let best = null;
    const tried = [];
    for (const fee of t.poolFees) {
      try {
        const raw = await rpc.call('eth_call', [{
          to: QUOTER,
          data: uniswap.quoteExactInputSingleCalldata({ tokenIn: USDC, tokenOut: WETH9, amountIn, fee }),
        }, 'latest']);
        const q = uniswap.decodeQuoteResult(raw);
        tried.push({ fee, amountOut: q.amountOut.toString() });
        if (q.amountOut > 0n && (!best || q.amountOut > best.amountOut)) best = { amountOut: q.amountOut, fee };
      } catch (e) {
        tried.push({ fee, error: e.message });
      }
    }
    if (!best) logger.warn('treasury_quote_unavailable', { amount_in_usdc: usd(amountIn), tried });
    return best;
  }

  async function waitForReceipt(txHash, budgetMs) {
    const deadline = now() + budgetMs;
    while (now() < deadline) {
      let receipt = null;
      try {
        receipt = await rpc.call('eth_getTransactionReceipt', [txHash]);
      } catch (e) { /* transient — keep polling within the budget */ }
      if (receipt) return receipt;
      await sleep(cfg.receiptPollMs);
    }
    return null;
  }

  /**
   * Sign + broadcast one treasury transaction and wait for its receipt.
   *
   * The submit lock is held for exactly the nonce read, the signing and the
   * single eth_sendRawTransaction — never for the receipt poll. A settlement
   * arriving mid-sip therefore queues behind one RPC round trip, not behind a
   * 30-second confirmation. Returns { receipt, txHash, gasSpentWei } or
   * throws a classified error.
   */
  async function sendTx({ actionId, to, data, gasLimitMax, fees, label }) {
    let estimated;
    try {
      estimated = evm.quantityToBigInt(await rpc.call('eth_estimateGas', [{ from: ME, to, data, value: '0x0' }]));
    } catch (e) {
      // A pre-flight revert costs nothing, which is exactly why we always
      // simulate: a sip that would fail on slippage/allowance/blacklist is
      // skipped instead of burning gas to discover it.
      const cls = uniswap.classifyRevert(e.message);
      const err = new Error(`${label} would revert: ${e.message}`);
      err.classification = cls;
      err.stage = 'estimate';
      throw err;
    }
    if (estimated > gasLimitMax) {
      const err = new Error(`${label} gas estimate ${estimated} exceeds the configured cap ${gasLimitMax}`);
      err.classification = { class: 'gas_cap', strike: true, hardDisable: false };
      err.stage = 'estimate';
      throw err;
    }
    let gasLimit = (estimated * 125n) / 100n;
    if (gasLimit > gasLimitMax) gasLimit = gasLimitMax;

    let sent;
    try {
      sent = await withSubmitLock(async () => {
        const txNonce = Number(evm.quantityToBigInt(await rpc.call('eth_getTransactionCount', [ME, 'pending'])));
        const signed = signer.signTx({
          chainId: cfg.chainId,
          nonce: txNonce,
          maxPriorityFeePerGas: fees.maxPriorityFeePerGas,
          maxFeePerGas: fees.maxFeePerGas,
          gasLimit,
          to,
          // NEVER attach value: ETH sent to SwapRouter02 stays there and is
          // claimable by anyone via refundETH().
          value: 0n,
          data,
        });
        // Persist the hash BEFORE broadcast, same discipline as settlements.
        tstore.attachTx(actionId, { txHash: signed.hash, txNonce });
        logger.info(`treasury_${label}_submitting`, { action_id: actionId, tx: signed.hash, tx_nonce: txNonce, gas_limit: gasLimit.toString(), max_fee_per_gas: fees.maxFeePerGas.toString() });
        await rpc.call('eth_sendRawTransaction', [signed.rawTx]);
        return { txHash: signed.hash, txNonce };
      });
    } catch (e) {
      const err = new Error(`${label} broadcast failed: ${e.message}`);
      // A transport error means the send MAY have landed: unknown, not failed.
      err.classification = e && e.transport
        ? { class: 'unknown_outcome', strike: false, hardDisable: false }
        : uniswap.classifyRevert(e.message);
      err.stage = 'broadcast';
      err.unknownOutcome = Boolean(e && e.transport);
      throw err;
    }

    const receipt = await waitForReceipt(sent.txHash, cfg.receiptTimeoutMs);
    if (!receipt) {
      const err = new Error(`${label} broadcast but no receipt within ${cfg.receiptTimeoutMs}ms`);
      err.classification = { class: 'unknown_outcome', strike: false, hardDisable: false };
      err.stage = 'receipt';
      err.unknownOutcome = true;
      err.txHash = sent.txHash;
      throw err;
    }
    const gasSpentWei = gasMod.receiptGasSpentWei(receipt);
    if (metrics && gasSpentWei > 0n) metrics.treasuryGasSpentWei.inc({ kind: label }, gasSpentWei);
    if (receipt.status !== '0x1') {
      const err = new Error(`${label} reverted on-chain (status ${receipt.status})`);
      err.classification = { class: 'reverted', strike: true, hardDisable: false };
      err.stage = 'receipt';
      err.txHash = sent.txHash;
      err.gasSpentWei = gasSpentWei;
      throw err;
    }
    return { receipt, txHash: sent.txHash, txNonce: sent.txNonce, gasSpentWei };
  }

  /* ------------------------------------------------------------------ sip -- */

  /**
   * One adaptive sip attempt. Never throws: every outcome is a report the
   * caller can log. `force` (manual CLI sip) bypasses the ETH floor, the
   * cooldown and a previous disable — it does NOT bypass the daily budget,
   * the minimum size, the slippage bound or the economic sanity check,
   * because those bound how much money a mistake can cost.
   */
  async function attemptSip({ trigger = 'interval', force = false, balances } = {}) {
    if (!t.enabled) return skipped('treasury_disabled');
    if (sippingDisabled && !force) return skipped('sipping_disabled', { detail: sippingDisabledReason });

    const bal = balances || (await readBalances());
    const { ethWei, usdcAtomic } = bal;

    const emergency = ethWei < t.ethFloorWei / 2n;
    if (!force && ethWei >= t.ethFloorWei) {
      return skipped('above_eth_floor', { eth_wei: ethWei.toString(), floor_wei: t.ethFloorWei.toString() });
    }

    // Cooldown counts ATTEMPTS, not successes: a failing sip must not be
    // retried in a tight loop. It halves below floor/2 so a wallet that is
    // actually about to run dry can refuel sooner than once a day.
    //
    // A FAILED attempt gets the much shorter retry cooldown instead. The
    // common failure is a stale quote, which reverts at the pre-flight
    // estimate and therefore costs nothing at all — making the refuel loop
    // wait a full day over one unlucky tick would be the expensive choice,
    // and the two-strike breaker still bounds genuine failures.
    const lastSip = tstore.last('sip');
    const lastAt = lastSip ? Number(lastSip.created_at) : 0;
    const fullCooldownS = emergency ? Math.floor(t.sipCooldownS / 2) : t.sipCooldownS;
    const cooldownS = lastSip && lastSip.status === 'failed'
      ? Math.min(t.retryCooldownS, fullCooldownS)
      : fullCooldownS;
    if (!force && lastAt && nowSec() - lastAt < cooldownS) {
      return skipped('cooldown', {
        cooldown_s: cooldownS,
        emergency,
        retry: Boolean(lastSip && lastSip.status === 'failed'),
        next_allowed_at: lastAt + cooldownS,
        seconds_remaining: lastAt + cooldownS - nowSec(),
      });
    }

    // ADAPTIVE SIZING — the bootstrap-stall cure.
    let amountIn = usdcAtomic < t.sipUsdcAtomic ? usdcAtomic : t.sipUsdcAtomic;
    if (amountIn < t.sipMinUsdcAtomic) {
      return skipped('insufficient_usdc', {
        usdc: usd(usdcAtomic),
        min_usdc: usd(t.sipMinUsdcAtomic),
        detail: 'not enough accrued revenue to buy a worthwhile amount of gas yet',
      });
    }

    // Daily budget: a hard cap on how much revenue can be converted per day,
    // whatever fires the trigger (interval, settlement burst, or both).
    const spentToday = tstore.sipSpendSince(nowSec() - DAY_SECONDS);
    const remaining = t.dailySwapBudgetAtomic > spentToday ? t.dailySwapBudgetAtomic - spentToday : 0n;
    if (remaining < t.sipMinUsdcAtomic) {
      return skipped('daily_budget_exhausted', {
        spent_today_usdc: usd(spentToday),
        budget_usdc: usd(t.dailySwapBudgetAtomic),
      });
    }
    if (amountIn > remaining) amountIn = remaining;

    let health;
    try {
      health = await tokenHealth();
    } catch (e) {
      return skipped('token_health_unknown', { detail: e.message });
    }
    if (health.paused || health.blacklisted) {
      return skipped('token_unavailable', { paused: health.paused, blacklisted: health.blacklisted });
    }

    const quote = await bestQuote(amountIn);
    if (!quote) return skipped('no_quote');
    // amountOutMinimum in BigInt atomic units: quote * (10000 - bps) / 10000.
    const minOut = (quote.amountOut * (10_000n - BigInt(t.maxSlippageBps))) / 10_000n;
    if (minOut <= 0n) return skipped('quote_dust', { quote_wei: quote.amountOut.toString() });

    let fees;
    try {
      fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
    } catch (e) {
      return skipped('fee_estimate_failed', { detail: e.message });
    }

    // Economic sanity: the ETH a sip buys must dwarf the gas the sip costs.
    // At today's Base fees the ratio is ~2,000x, so anything under ~20x means
    // something is badly wrong (fee spike, dust sip, wrong fee tier) and the
    // right move is to skip rather than to convert revenue at a loss.
    const worstGasWei = (BigInt(t.maxApproveGas) + BigInt(t.maxSwapGas)) * fees.maxFeePerGas;
    if (minOut < worstGasWei * BigInt(t.minEthOutGasRatio)) {
      return skipped('uneconomic', {
        min_out_wei: minOut.toString(),
        worst_gas_wei: worstGasWei.toString(),
        required_ratio: t.minEthOutGasRatio,
      });
    }

    // ---- allowance (exact amount, per sip: no standing approval survives)
    try {
      const allowance = uniswap.decodeUint(
        await rpc.call('eth_call', [{ to: USDC, data: uniswap.allowanceCalldata(ME, ROUTER) }, 'latest'])
      );
      if (allowance < amountIn) {
        const approveId = tstore.begin({ kind: 'approve', trigger, usdcAmount: amountIn, destination: ROUTER });
        try {
          const res = await sendTx({
            actionId: approveId,
            to: USDC,
            data: uniswap.approveCalldata(ROUTER, amountIn),
            gasLimitMax: t.maxApproveGas,
            fees,
            label: 'approve',
          });
          tstore.confirm(approveId, { gasSpentWei: res.gasSpentWei, txHash: res.txHash });
          // Confirm the allowance really landed before spending gas on a swap
          // that would otherwise revert with STF.
          const after = uniswap.decodeUint(
            await rpc.call('eth_call', [{ to: USDC, data: uniswap.allowanceCalldata(ME, ROUTER) }, 'latest'])
          );
          if (after < amountIn) {
            tstore.fail(approveId, `allowance still ${after} after approve`);
            recordFailure('sip', 'allowance', { hardDisable: false });
            logger.error('treasury_approve_ineffective', { action_id: approveId, allowance: after.toString(), needed: amountIn.toString() });
            return { action: 'failed', reason: 'allowance', detail: 'approve confirmed but allowance did not increase' };
          }
        } catch (e) {
          const cls = e.classification || { class: 'unknown', strike: true, hardDisable: false };
          if (e.unknownOutcome) {
            // The approve may have landed; leave the row 'submitting' and try
            // again on a later tick rather than counting a failure we cannot
            // prove (and never broadcast the swap that would then STF).
            logger.warn('treasury_approve_unknown_outcome', { action_id: approveId, tx: e.txHash, stage: e.stage, detail: e.message });
            return { action: 'unknown', reason: `approve_${cls.class}`, action_id: approveId, detail: e.message };
          }
          tstore.fail(approveId, e.message, { gasSpentWei: e.gasSpentWei, txHash: e.txHash });
          recordFailure('sip', `approve_${cls.class}`, cls);
          logger.error('treasury_approve_failed', { action_id: approveId, stage: e.stage, class: cls.class, detail: e.message });
          return { action: 'failed', reason: `approve_${cls.class}`, detail: e.message };
        }
      }
    } catch (e) {
      return skipped('allowance_read_failed', { detail: e.message });
    }

    // ---- the swap itself: one atomic multicall, deadline-bounded
    const deadline = BigInt(nowSec() + t.swapDeadlineS);
    const data = uniswap.sipCalldata({
      usdc: USDC,
      weth9: WETH9,
      fee: quote.fee,
      amountIn,
      amountOutMinimum: minOut,
      recipient: ME, // never a router sentinel — unwrapWETH9 does not substitute them
      deadline,
    });
    const actionId = tstore.begin({
      kind: 'sip',
      trigger,
      usdcAmount: amountIn,
      quoteWei: quote.amountOut,
      minOutWei: minOut,
      poolFee: quote.fee,
      destination: ROUTER,
    });

    let res;
    try {
      res = await sendTx({ actionId, to: ROUTER, data, gasLimitMax: t.maxSwapGas, fees, label: 'sip' });
    } catch (e) {
      const cls = e.classification || { class: 'unknown', strike: true, hardDisable: false };
      if (e.unknownOutcome) {
        // The tx may have landed. Leave the row 'submitting' so the daily
        // budget keeps counting it and the operator can see it in history;
        // do NOT strike, and let the cooldown prevent a hot retry.
        logger.warn('treasury_sip_unknown_outcome', { action_id: actionId, tx: e.txHash, stage: e.stage, detail: e.message });
        if (metrics) metrics.treasurySipsTotal.inc({ result: 'unknown' });
        return { action: 'unknown', reason: cls.class, action_id: actionId, detail: e.message };
      }
      tstore.fail(actionId, e.message, { gasSpentWei: e.gasSpentWei, txHash: e.txHash });
      recordFailure('sip', cls.class, cls);
      if (metrics) metrics.treasurySipsTotal.inc({ result: 'failed' });
      logger.error('treasury_sip_failed', {
        action_id: actionId, tx: e.txHash, stage: e.stage, class: cls.class,
        amount_usdc: usd(amountIn), min_out_wei: minOut.toString(), detail: e.message,
      });
      return { action: 'failed', reason: cls.class, action_id: actionId, detail: e.message };
    }

    // ---- verify the sip actually happened, from chain truth
    const wad = uniswap.findWethWithdrawal(res.receipt, { weth9: WETH9, router: ROUTER });
    const usdcLeft = uniswap.hasTransfer(res.receipt, { token: USDC, from: ME, value: amountIn });
    if (wad === null || wad < minOut || !usdcLeft) {
      const detail = wad === null
        ? 'no WETH9 Withdrawal log in our own receipt'
        : (!usdcLeft ? 'no USDC Transfer of the sip amount out of the facilitator wallet' : `unwrapped ${wad} wei < amountOutMinimum ${minOut}`);
      tstore.fail(actionId, `receipt_effect_mismatch: ${detail}`, { gasSpentWei: res.gasSpentWei, txHash: res.txHash });
      recordFailure('sip', 'effect_mismatch');
      if (metrics) metrics.treasurySipsTotal.inc({ result: 'failed' });
      logger.error('treasury_sip_effect_mismatch', { action_id: actionId, tx: res.txHash, detail });
      return { action: 'failed', reason: 'effect_mismatch', action_id: actionId, detail };
    }

    tstore.confirm(actionId, { ethReceivedWei: wad, gasSpentWei: res.gasSpentWei, txHash: res.txHash });
    recordSuccess('sip');
    if (metrics) {
      metrics.treasurySipsTotal.inc({ result: 'ok' });
      metrics.treasurySipEthReceivedWei.inc({}, wad);
      metrics.treasurySippedUsdcTotal.inc({}, amountIn);
    }
    logger.info('treasury_sip_settled', {
      action_id: actionId,
      tx: res.txHash,
      explorer: cfg.explorerTx ? cfg.explorerTx + res.txHash : undefined,
      trigger,
      emergency,
      amount_usdc: usd(amountIn),
      pool_fee: quote.fee,
      quote_wei: quote.amountOut.toString(),
      min_out_wei: minOut.toString(),
      eth_received_wei: wad.toString(),
      gas_spent_wei: res.gasSpentWei.toString(),
    });

    // Refresh the gauges. eth_getBalance immediately after a receipt can
    // still read the pre-tx value, so this is a best-effort gauge update —
    // the authoritative "did the sip work" answer is the Withdrawal log above.
    try { await readBalances(); } catch (e) { /* gauges only */ }

    return {
      action: 'sipped',
      action_id: actionId,
      tx: res.txHash,
      amount_usdc_atomic: amountIn,
      eth_received_wei: wad,
      pool_fee: quote.fee,
      emergency,
    };
  }

  /* ---------------------------------------------------------------- sweep -- */

  /**
   * Drain USDC above the ceiling to the cold address. The destination is the
   * construction-time COLD binding and nothing else: this function takes no
   * destination parameter, and adding one would be the bug the immutability
   * test exists to catch.
   */
  async function attemptSweep({ trigger = 'interval', force = false, balances } = {}) {
    if (!t.enabled) return skipped('treasury_disabled');
    if (!COLD) return skipped('no_cold_address');
    if (sweepingDisabled && !force) return skipped('sweeping_disabled', { detail: sweepingDisabledReason });

    const bal = balances || (await readBalances());
    const { usdcAtomic } = bal;
    if (usdcAtomic <= t.usdcCeilingAtomic) {
      return skipped('below_ceiling', { usdc: usd(usdcAtomic), ceiling_usdc: usd(t.usdcCeilingAtomic) });
    }
    const surplus = usdcAtomic - t.usdcCeilingAtomic;
    if (!force && surplus < t.minSweepAtomic) {
      return skipped('below_min_sweep', { surplus_usdc: usd(surplus), min_sweep_usdc: usd(t.minSweepAtomic) });
    }

    let health;
    try {
      health = await tokenHealth();
    } catch (e) {
      return skipped('token_health_unknown', { detail: e.message });
    }
    if (health.paused || health.blacklisted) {
      return skipped('token_unavailable', { paused: health.paused, blacklisted: health.blacklisted });
    }

    let fees;
    try {
      fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
    } catch (e) {
      return skipped('fee_estimate_failed', { detail: e.message });
    }

    const actionId = tstore.begin({ kind: 'sweep', trigger, usdcAmount: surplus, destination: COLD });
    let res;
    try {
      res = await sendTx({
        actionId,
        to: USDC,
        data: uniswap.transferCalldata(COLD, surplus),
        gasLimitMax: t.maxSweepGas,
        fees,
        label: 'sweep',
      });
    } catch (e) {
      const cls = e.classification || { class: 'unknown', strike: true, hardDisable: false };
      if (e.unknownOutcome) {
        logger.warn('treasury_sweep_unknown_outcome', { action_id: actionId, tx: e.txHash, detail: e.message });
        if (metrics) metrics.treasurySweepsTotal.inc({ result: 'unknown' });
        return { action: 'unknown', reason: cls.class, action_id: actionId, detail: e.message };
      }
      tstore.fail(actionId, e.message, { gasSpentWei: e.gasSpentWei, txHash: e.txHash });
      recordFailure('sweep', cls.class, cls);
      if (metrics) metrics.treasurySweepsTotal.inc({ result: 'failed' });
      logger.error('treasury_sweep_failed', { action_id: actionId, tx: e.txHash, stage: e.stage, class: cls.class, amount_usdc: usd(surplus), detail: e.message });
      return { action: 'failed', reason: cls.class, action_id: actionId, detail: e.message };
    }

    if (!uniswap.hasTransfer(res.receipt, { token: USDC, from: ME, to: COLD, value: surplus })) {
      tstore.fail(actionId, 'receipt_effect_mismatch: no USDC Transfer to the cold address for the swept amount', {
        gasSpentWei: res.gasSpentWei, txHash: res.txHash,
      });
      recordFailure('sweep', 'effect_mismatch');
      if (metrics) metrics.treasurySweepsTotal.inc({ result: 'failed' });
      logger.error('treasury_sweep_effect_mismatch', { action_id: actionId, tx: res.txHash });
      return { action: 'failed', reason: 'effect_mismatch', action_id: actionId };
    }

    tstore.confirm(actionId, { gasSpentWei: res.gasSpentWei, txHash: res.txHash });
    recordSuccess('sweep');
    if (metrics) {
      metrics.treasurySweepsTotal.inc({ result: 'ok' });
      metrics.treasurySweptUsdcTotal.inc({}, surplus);
    }
    logger.info('treasury_sweep_settled', {
      action_id: actionId,
      tx: res.txHash,
      explorer: cfg.explorerTx ? cfg.explorerTx + res.txHash : undefined,
      trigger,
      amount_usdc: usd(surplus),
      destination: COLD,
      gas_spent_wei: res.gasSpentWei.toString(),
    });
    try { await readBalances(); } catch (e) { /* gauges only */ }

    return { action: 'swept', action_id: actionId, tx: res.txHash, amount_usdc_atomic: surplus, destination: COLD };
  }

  /* ----------------------------------------------------------------- tick -- */

  /** One full check. Never throws — the caller is a timer or a hook. */
  async function tick({ trigger = 'interval' } = {}) {
    const report = { trigger, at: now(), eth_wei: null, usdc_atomic: null, sip: null, sweep: null, error: null };
    try {
      let balances = await readBalances();
      report.eth_wei = balances.ethWei.toString();
      report.usdc_atomic = balances.usdcAtomic.toString();
      report.sip = await attemptSip({ trigger, balances });
      // A successful sip spent USDC — re-read before deciding on a sweep so
      // we never sweep money the sip already converted.
      if (report.sip && report.sip.action === 'sipped') balances = await readBalances();
      report.sweep = await attemptSweep({ trigger, balances });
    } catch (e) {
      report.error = e.message;
      logger.error('treasury_tick_failed', { trigger, detail: e.message });
    }
    lastTick = report;
    return report;
  }

  /**
   * Run a tick, coalescing concurrent triggers. Never runs two ticks at once
   * (one nonce lane, one budget) and never queues more than one follow-up.
   */
  function run(trigger) {
    if (inFlight) {
      queuedTrigger = queuedTrigger || trigger;
      return inFlight;
    }
    inFlight = (async () => {
      try {
        return await tick({ trigger });
      } finally {
        inFlight = null;
        if (queuedTrigger) {
          const next = queuedTrigger;
          queuedTrigger = null;
          const p = run(next);
          if (p && p.catch) p.catch(() => {});
        }
      }
    })();
    return inFlight;
  }

  /**
   * The post-settlement hook. Fire-and-forget by construction: it returns
   * synchronously, swallows every error, and the settlement path must never
   * await it. A settlement is finished the moment its receipt is confirmed;
   * treasury work happens afterwards, on its own.
   */
  function notifySettlement() {
    if (!t.enabled) return;
    try {
      const p = run('settlement');
      if (p && p.catch) p.catch(() => {});
    } catch (e) {
      // Nothing the treasury does may surface in the settlement path.
      try { logger.error('treasury_notify_failed', { detail: e.message }); } catch (e2) { /* give up quietly */ }
    }
  }

  function start() {
    if (!t.enabled || timer) return;
    timer = setInterval(() => {
      const p = run('interval');
      if (p && p.catch) p.catch(() => {});
    }, t.checkIntervalS * SEC);
    if (typeof timer.unref === 'function') timer.unref();
    if (metrics) {
      metrics.treasurySippingEnabled.set({}, sippingDisabled ? 0 : 1);
      metrics.treasurySweepingEnabled.set({}, sweepingDisabled ? 0 : 1);
    }
    logger.info('treasury_started', {
      cold_address: COLD,
      facilitator: ME,
      network: cfg.network,
      eth_floor_wei: t.ethFloorWei.toString(),
      sip_usdc: usd(t.sipUsdcAtomic),
      sip_min_usdc: usd(t.sipMinUsdcAtomic),
      ceiling_usdc: usd(t.usdcCeilingAtomic),
      daily_budget_usdc: usd(t.dailySwapBudgetAtomic),
      cooldown_s: t.sipCooldownS,
      interval_s: t.checkIntervalS,
      slippage_bps: t.maxSlippageBps,
      pool_fees: t.poolFees.join(','),
      router: ROUTER,
    });
    // Check once at boot: a wallet that ran out of ETH overnight should not
    // wait a full interval before it may refuel.
    const p = run('startup');
    if (p && p.catch) p.catch(() => {});
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  /** Non-fatal condition for /readyz. Null when everything is nominal. */
  function warning() {
    if (!t.enabled) return null;
    const parts = [];
    if (sippingDisabled) parts.push(sippingDisabledReason || 'sipping disabled');
    if (sweepingDisabled) parts.push(sweepingDisabledReason || 'sweeping disabled');
    return parts.length ? parts.join('; ') : null;
  }

  function status() {
    const spentToday = tstore.sipSpendSince(nowSec() - DAY_SECONDS);
    const lastAt = tstore.lastSipAt();
    return {
      enabled: t.enabled,
      network: cfg.network,
      single_wallet: evm.addressEquals(cfg.settlementAddress, ME),
      facilitator: ME,
      cold_address: COLD,
      contracts: { usdc: USDC, weth9: WETH9, router: ROUTER, quoter: QUOTER },
      policy: {
        eth_floor_wei: t.ethFloorWei.toString(),
        sip_usdc: usd(t.sipUsdcAtomic),
        sip_min_usdc: usd(t.sipMinUsdcAtomic),
        sip_cooldown_s: t.sipCooldownS,
        daily_swap_budget_usdc: usd(t.dailySwapBudgetAtomic),
        usdc_ceiling: usd(t.usdcCeilingAtomic),
        min_sweep_usdc: usd(t.minSweepAtomic),
        max_slippage_bps: t.maxSlippageBps,
        pool_fees: t.poolFees.slice(),
        check_interval_s: t.checkIntervalS,
        swap_deadline_s: t.swapDeadlineS,
      },
      sipping_disabled: sippingDisabled,
      sipping_disabled_reason: sippingDisabledReason || null,
      sip_consecutive_failures: sipFailures,
      sweeping_disabled: sweepingDisabled,
      sweeping_disabled_reason: sweepingDisabledReason || null,
      sweep_consecutive_failures: sweepFailures,
      spent_today_usdc: usd(spentToday),
      budget_remaining_usdc: usd(t.dailySwapBudgetAtomic > spentToday ? t.dailySwapBudgetAtomic - spentToday : 0n),
      last_sip_at: lastAt || null,
      next_sip_allowed_at: lastAt ? lastAt + t.sipCooldownS : null,
      balances: {
        eth_wei: lastBalances.ethWei === null ? null : lastBalances.ethWei.toString(),
        usdc: lastBalances.usdcAtomic === null ? null : usd(lastBalances.usdcAtomic),
        read_at: lastBalances.at || null,
      },
      totals: (() => {
        const x = tstore.totals();
        return {
          sips: x.sips,
          sips_failed: x.sipsFailed,
          sweeps: x.sweeps,
          sweeps_failed: x.sweepsFailed,
          approvals: x.approvals,
          sipped_usdc: usd(x.sippedUsdcAtomic),
          swept_usdc: usd(x.sweptUsdcAtomic),
          eth_received_wei: x.ethReceivedWei.toString(),
          gas_spent_wei: x.gasSpentWei.toString(),
        };
      })(),
      last_tick: lastTick,
      warning: warning(),
    };
  }

  /** Clear a two-strike disable after the operator fixed the cause. */
  function resume() {
    const was = { sipping_disabled: sippingDisabled, sweeping_disabled: sweepingDisabled };
    sippingDisabled = false;
    sippingDisabledReason = null;
    sipFailures = 0;
    sweepingDisabled = false;
    sweepingDisabledReason = null;
    sweepFailures = 0;
    persistSipState();
    persistSweepState();
    if (metrics) {
      metrics.treasurySippingEnabled.set({}, 1);
      metrics.treasurySweepingEnabled.set({}, 1);
    }
    logger.warn('treasury_resumed', was);
    return was;
  }

  return {
    // identity/config surface (read-only copies of the frozen bindings)
    coldAddress: COLD,
    facilitatorAddress: ME,
    contracts: { usdc: USDC, weth9: WETH9, router: ROUTER, quoter: QUOTER },
    enabled: t.enabled,

    start,
    stop,
    tick,
    run,
    notifySettlement,
    sipNow: (opts = {}) => attemptSip({ trigger: 'manual', force: true, ...opts }),
    sweepNow: (opts = {}) => attemptSweep({ trigger: 'manual', force: true, ...opts }),
    attemptSip,
    attemptSweep,
    readBalances,
    tokenHealth,
    bestQuote,
    status,
    warning,
    resume,
  };
}

module.exports = { createTreasury, DAY_SECONDS };
