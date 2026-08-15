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
 *     the settlement path fires and forgets. It holds the settlement engine's
 *     submit lock for exactly one sign+broadcast — never across receipt
 *     polling — and it takes its nonce from the SAME allocator the settlement
 *     engine uses (facilitator-evm/nonce.js), because a shared lock around
 *     two independent `eth_getTransactionCount(...,'pending')` reads does not
 *     stop a lagging RPC front-end from handing both writers one nonce.
 *   - Nothing new is signed while a previous treasury transaction is
 *     unresolved. Transactions from one EOA are included in nonce order, so a
 *     treasury transaction stuck in the mempool would block every settlement
 *     behind it. recover() resolves such rows from chain truth and, when they
 *     are genuinely stuck, rebroadcasts or fee-bumps them (same nonce, same
 *     intent) until the lane clears.
 *   - The sweep destination is captured at construction from server config
 *     and is otherwise unreachable: no argument, no request field, no later
 *     config mutation can retarget it. Neither entry point accepts a balance
 *     from its caller either — sizes come from a fresh chain read.
 *   - Two consecutive swap FAILURES disable sipping and raise a /readyz
 *     WARNING. Paid traffic keeps settling while ETH lasts; the failure mode
 *     is "operator tops up manually", never "block payments" and never "loop
 *     swaps". A refuel that is repeatedly SKIPPED (uneconomic quote, no
 *     liquidity, exhausted budget) while ETH is under the floor raises the
 *     same warning: "cannot refuel" is exactly what the operator must see.
 *   - Every amount is a BigInt atomic unit (USDC 6 decimals, ETH wei). No
 *     float touches money, and a quote never round-trips through Number.
 */

const os = require('node:os');
const crypto = require('node:crypto');

const evm = require('../facilitator-evm/evm');
const gasMod = require('../facilitator-evm/gas');
const { createNonceAllocator } = require('../facilitator-evm/nonce');
const uniswap = require('./uniswap');
const { atomicToDecimalString } = require('../metrics');

const SEC = 1000;
const USDC_DECIMALS = 6;
const DAY_SECONDS = 86_400;

/**
 * Live-measured gas for each treasury transaction shape (recon §7, Base
 * mainnet). These are the REALISTIC cost used by the economic sanity check —
 * X402_TREASURY_MAX_*_GAS are caps, and pricing a sip at the caps times
 * 2*baseFee (the fee ceiling, not the fee paid) demanded roughly 90x the ETH
 * a sip really costs and silently vetoed the whole mechanism at ordinary Base
 * fees.
 */
const MEASURED_GAS = {
  approve: 60_000n,   // 56,240 cold
  swap: 180_000n,     // 165,389 multicall
  sweep: 70_000n,     // ~63,000 ERC-20 transfer
};

/** Minimum replacement-fee bump most clients require (12.5%); we use 25%. */
const BUMP_NUM = 125n;
const BUMP_DEN = 100n;

/** Outcome of an attempt that never ran (not a failure — nothing was spent). */
function skipped(reason, extra = {}) {
  return Object.assign({ action: 'skipped', reason }, extra);
}

/** Skip reasons that mean "the wallet could not be refuelled", not "no need". */
const REFUEL_BLOCKED_REASONS = new Set([
  'uneconomic', 'no_quote', 'quote_dust', 'insufficient_usdc', 'daily_budget_exhausted',
  'token_unavailable', 'fee_estimate_failed', 'allowance_read_failed', 'token_health_unknown',
  'quote_off_reference', 'quote_off_last_realized', 'gas_budget_exhausted', 'unresolved_action',
  'sipping_disabled',
]);

function createTreasury({
  cfg,
  rpc,
  tstore,
  signer,
  metrics,
  logger,
  // The settlement engine's FIFO submit lock. Sharing it is what serialises
  // the two writers; sharing `nonces` is what makes the nonce lane coherent.
  // Both default to standalone equivalents so unit tests can drive the module
  // on its own.
  withSubmitLock = (fn) => Promise.resolve().then(fn),
  nonces = null,
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

  const nonceLane = nonces || createNonceAllocator({ rpc, address: ME, now, logger });

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
    // config.js rejects the reserved low-address range before we get here
    // (those have no checksum to fail, so the EIP-55 "typo guard" never sees
    // them); re-assert it, because createTreasury is also reachable from
    // tooling that builds a cfg by hand.
    if (BigInt(COLD) < 0x10000n) {
      throw new Error(`treasury: X402_TREASURY_COLD_ADDRESS ${COLD} is in the reserved low-address range (zero address / precompiles)`);
    }
    // This list catches a cold address pointed at a contract we know about.
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

  // Cold-address code check (see verifyColdAddress): null = not checked yet.
  let coldCheck = null;
  // Consecutive ticks where the wallet was under the ETH floor and the sip
  // could not run. This is the "cannot refuel" signal, and it is a WARNING
  // rather than a breaker because refusing to swap is the safe direction.
  let refuelBlockedStreak = 0;
  let refuelBlockedReason = null;
  // Cross-process lease (service vs `animica-x402 treasury … --confirm`).
  const leaseOwner = `${os.hostname()}:${process.pid}:${crypto.randomBytes(4).toString('hex')}`;
  let leaseHeld = false;
  // Only a STARTED treasury insists on holding the lease. Standalone use (the
  // CLI, a unit test) has its own interlock — bin/animica-x402 takes the lease
  // itself and refuses when the service holds it — and must not be blocked by
  // a lease it never tried to take.
  let leaseRequired = false;

  const nowSec = () => Math.floor(now() / 1000);
  const usd = (atomic) => atomicToDecimalString(atomic, USDC_DECIMALS);

  // Publish the breaker gauges immediately: an unset gauge renders as 0,
  // which would read as "sipping disabled" on a perfectly healthy process.
  if (metrics) {
    metrics.treasurySippingEnabled.set({}, sippingDisabled ? 0 : 1);
    metrics.treasurySweepingEnabled.set({}, sweepingDisabled ? 0 : 1);
    if (metrics.treasuryRefuelBlocked) metrics.treasuryRefuelBlocked.set({}, 0);
    if (metrics.treasuryUnresolvedActions) metrics.treasuryUnresolvedActions.set({}, tstore.unresolvedCount());
  }

  /** Keep the "unknown outcome in the nonce lane" gauge honest. */
  function publishUnresolved() {
    if (metrics && metrics.treasuryUnresolvedActions) {
      metrics.treasuryUnresolvedActions.set({}, tstore.unresolvedCount());
    }
  }

  async function withGauge(fn) {
    try {
      return await fn();
    } finally {
      publishUnresolved();
    }
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
      sweepingDisabledReason = hardDisable
        ? `hard-disabled: ${reason}`
        : `disabled after ${sweepFailures} consecutive sweep failures (last: ${reason})`;
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

  /**
   * One eth_getCode on the cold address. An EIP-55 checksum cannot tell a
   * wrong-but-valid address from the right one, and it cannot tell an EOA
   * from a contract that reverts on (or silently swallows) an ERC-20
   * transfer. A contract destination is refused unless the operator opted in
   * with X402_TREASURY_COLD_ALLOW_CONTRACT=1 (safes/multisigs are legitimate
   * destinations — they just have to be declared).
   */
  async function verifyColdAddress() {
    if (!COLD) return { ok: false, reason: 'no_cold_address' };
    if (coldCheck && coldCheck.ok) return coldCheck;
    let code;
    try {
      code = await rpc.call('eth_getCode', [COLD, 'latest']);
    } catch (e) {
      // Unknown: do not sweep into an address we could not check, but do not
      // burn a strike either — the RPC is the thing that failed.
      return { ok: false, reason: 'cold_address_unverified', detail: e.message };
    }
    const hasCode = typeof code === 'string' && evm.strip0x(code).replace(/0+$/, '') !== '';
    if (hasCode && !t.coldAllowContract) {
      coldCheck = {
        ok: false,
        reason: 'cold_address_is_contract',
        detail: `${COLD} has contract code; set X402_TREASURY_COLD_ALLOW_CONTRACT=1 if that is a safe/multisig you control`,
      };
      if (!sweepingDisabled) {
        recordFailure('sweep', coldCheck.detail, { hardDisable: true, strike: false });
      }
      return coldCheck;
    }
    coldCheck = { ok: true, hasCode };
    return coldCheck;
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

  /* -------------------------------------------------------- gas budget -- */

  let gasSpendStmt = null;
  /**
   * Trailing-24h ETH spend by THIS ACCOUNT — settlements and treasury
   * transactions together. X402_DAILY_GAS_BUDGET_WEI is documented as a daily
   * ETH ceiling, so treasury gas has to count against it (and the treasury
   * has to stop spending when the breaker is open).
   */
  function accountGasSince(sinceSec) {
    let total = tstore.gasSpentSince(sinceSec);
    try {
      if (!gasSpendStmt) gasSpendStmt = tstore.db.prepare('SELECT spent_wei FROM gas_spend WHERE created_at >= ?');
      for (const row of gasSpendStmt.all(Number(sinceSec))) {
        try { total += BigInt(row.spent_wei); } catch (e) { /* ignore malformed */ }
      }
    } catch (e) {
      // No payments table in this DB handle — treasury spend alone then.
    }
    return total;
  }

  /** null when the transaction fits the daily budget, else a skip payload. */
  function gasBudgetSkip(reserveWei, label) {
    if (!cfg.dailyGasBudgetWei || cfg.dailyGasBudgetWei <= 0n) return null;
    const spent = accountGasSince(nowSec() - DAY_SECONDS);
    const projected = spent + BigInt(reserveWei || 0n);
    if (projected >= cfg.dailyGasBudgetWei) {
      logger.warn('treasury_gas_budget_exhausted', {
        label,
        spent_wei: spent.toString(),
        reserve_wei: String(reserveWei),
        budget_wei: cfg.dailyGasBudgetWei.toString(),
      });
      return skipped('gas_budget_exhausted', {
        spent_wei: spent.toString(),
        reserve_wei: String(reserveWei),
        budget_wei: cfg.dailyGasBudgetWei.toString(),
      });
    }
    return null;
  }

  /* ----------------------------------------------------------- send tx -- */

  /**
   * Sign + broadcast one treasury transaction and wait for its receipt.
   *
   * The submit lock is held for exactly the nonce allocation, the signing and
   * the single eth_sendRawTransaction — never for the receipt poll. A
   * settlement arriving mid-sip therefore queues behind one RPC round trip,
   * not behind a 30-second confirmation. The nonce comes from the shared
   * allocator, so it can never duplicate a settlement's.
   *
   * Returns { receipt, txHash, gasSpentWei } or throws a classified error.
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
        const alloc = await nonceLane.next();
        const txNonce = alloc.nonce;
        if (alloc.source === 'high_water') {
          logger.warn('nonce_rpc_lagging', {
            action_id: actionId, remote_pending: alloc.remotePending, using_nonce: txNonce,
            detail: 'the RPC pending count is behind our own last broadcast; using the local high-water mark',
          });
        }
        const txParams = {
          to,
          data,
          gasLimit: gasLimit.toString(),
          maxFeePerGas: fees.maxFeePerGas.toString(),
          maxPriorityFeePerGas: fees.maxPriorityFeePerGas.toString(),
          nonce: txNonce,
        };
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
        // Persist the hash AND the signed bytes BEFORE broadcast, same
        // discipline as settlements: recovery can then rebroadcast or bump
        // exactly what may be in flight instead of stranding the nonce lane.
        nonceLane.commit(txNonce);
        tstore.attachTx(actionId, { txHash: signed.hash, txNonce, rawTx: signed.rawTx, txParams });
        publishUnresolved();
        logger.info(`treasury_${label}_submitting`, { action_id: actionId, tx: signed.hash, tx_nonce: txNonce, gas_limit: gasLimit.toString(), max_fee_per_gas: fees.maxFeePerGas.toString() });
        try {
          await rpc.call('eth_sendRawTransaction', [signed.rawTx]);
        } catch (e) {
          // A definitive rejection means nothing is in flight: give the nonce
          // back so the next signer does not leave a permanent gap. A
          // transport error is an unknown outcome and keeps it.
          if (!(e && e.transport)) nonceLane.release(txNonce);
          throw e;
        }
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

  /* ---------------------------------------------------------- recovery -- */

  /** Apply chain truth to one confirmed receipt, per action kind. */
  function settleRowFromReceipt(row, receipt) {
    const gasSpentWei = gasMod.receiptGasSpentWei(receipt);
    if (metrics && gasSpentWei > 0n) metrics.treasuryGasSpentWei.inc({ kind: row.kind }, gasSpentWei);
    if (receipt.status !== '0x1') {
      tstore.fail(row.action_id, `reverted on-chain (status ${receipt.status})`, { gasSpentWei, txHash: row.tx_hash });
      if (metrics) {
        if (row.kind === 'sip') metrics.treasurySipsTotal.inc({ result: 'failed' });
        if (row.kind === 'sweep') metrics.treasurySweepsTotal.inc({ result: 'failed' });
      }
      logger.error('treasury_recovered_reverted', { action_id: row.action_id, kind: row.kind, tx: row.tx_hash });
      return { outcome: 'failed' };
    }
    if (row.kind === 'sip') {
      const wad = uniswap.findWethWithdrawal(receipt, { weth9: WETH9, router: ROUTER });
      const amount = row.usdc_amount ? BigInt(row.usdc_amount) : 0n;
      const left = uniswap.hasTransfer(receipt, { token: USDC, from: ME, value: amount });
      if (wad === null || !left) {
        tstore.fail(row.action_id, 'receipt_effect_mismatch on recovery', { gasSpentWei, txHash: row.tx_hash });
        if (metrics) metrics.treasurySipsTotal.inc({ result: 'failed' });
        return { outcome: 'failed' };
      }
      tstore.confirm(row.action_id, { ethReceivedWei: wad, gasSpentWei, txHash: row.tx_hash });
      if (metrics) {
        metrics.treasurySipsTotal.inc({ result: 'ok' });
        metrics.treasurySipEthReceivedWei.inc({}, wad);
        metrics.treasurySippedUsdcTotal.inc({}, amount);
      }
      logger.info('treasury_recovered_sip', {
        action_id: row.action_id, tx: row.tx_hash, amount_usdc: usd(amount),
        eth_received_wei: wad.toString(), gas_spent_wei: gasSpentWei.toString(),
      });
      return { outcome: 'confirmed' };
    }
    if (row.kind === 'sweep') {
      const amount = row.usdc_amount ? BigInt(row.usdc_amount) : 0n;
      const ok = uniswap.hasTransfer(receipt, { token: USDC, from: ME, to: COLD, value: amount });
      if (!ok) {
        tstore.fail(row.action_id, 'receipt_effect_mismatch on recovery', { gasSpentWei, txHash: row.tx_hash });
        if (metrics) metrics.treasurySweepsTotal.inc({ result: 'failed' });
        return { outcome: 'failed' };
      }
      tstore.confirm(row.action_id, { gasSpentWei, txHash: row.tx_hash });
      if (metrics) {
        metrics.treasurySweepsTotal.inc({ result: 'ok' });
        metrics.treasurySweptUsdcTotal.inc({}, amount);
      }
      logger.info('treasury_recovered_sweep', { action_id: row.action_id, tx: row.tx_hash, amount_usdc: usd(amount) });
      return { outcome: 'confirmed' };
    }
    tstore.confirm(row.action_id, { gasSpentWei, txHash: row.tx_hash });
    return { outcome: 'confirmed' };
  }

  /** Re-sign the same nonce at a higher fee (the stuck-transaction escape). */
  async function bumpStuck(row) {
    let params = null;
    try {
      if (row.tx_params) params = JSON.parse(row.tx_params);
    } catch (e) {
      params = null;
    }
    // Rows written before this column existed (or a truncated write) cannot be
    // re-signed. Say so plainly: the operator has to replace that nonce by
    // hand, and the log line below carries it.
    if (!params || params.maxFeePerGas === undefined || params.nonce === undefined) {
      return { outcome: 'unbumpable', detail: 'no stored tx params (row predates the bump support)' };
    }
    if (Number(row.bump_count || 0) >= t.maxTxBumps) {
      return { outcome: 'unbumpable', detail: `already bumped ${row.bump_count} times` };
    }
    const oldMax = BigInt(params.maxFeePerGas);
    const oldTip = BigInt(params.maxPriorityFeePerGas);
    let maxFee = (oldMax * BUMP_NUM) / BUMP_DEN + 1n;
    let tip = (oldTip * BUMP_NUM) / BUMP_DEN + 1n;
    if (maxFee > cfg.maxFeePerGasWei) maxFee = cfg.maxFeePerGasWei;
    if (tip > maxFee) tip = maxFee;
    // Below ~12.5% no client accepts the replacement — do not waste a send.
    if (maxFee * 1000n < oldMax * 1125n) {
      return { outcome: 'unbumpable', detail: `X402_MAX_FEE_PER_GAS_WEI ${cfg.maxFeePerGasWei} leaves no room to bump ${oldMax}` };
    }
    const nextParams = { ...params, maxFeePerGas: maxFee.toString(), maxPriorityFeePerGas: tip.toString() };
    const signed = await withSubmitLock(async () => {
      const s = signer.signTx({
        chainId: cfg.chainId,
        nonce: Number(params.nonce),
        maxPriorityFeePerGas: tip,
        maxFeePerGas: maxFee,
        gasLimit: BigInt(params.gasLimit),
        to: params.to,
        value: 0n,
        data: params.data,
      });
      nonceLane.commit(Number(params.nonce));
      tstore.bumped(row.action_id, { txHash: s.hash, rawTx: s.rawTx, txParams: nextParams });
      await rpc.call('eth_sendRawTransaction', [s.rawTx]);
      return s;
    });
    logger.warn('treasury_tx_bumped', {
      action_id: row.action_id, kind: row.kind, tx_nonce: params.nonce,
      old_tx: row.tx_hash, tx: signed.hash,
      old_max_fee_per_gas: oldMax.toString(), max_fee_per_gas: maxFee.toString(),
      detail: 'a stuck treasury transaction blocks every later settlement behind its nonce — replacing it',
    });
    return { outcome: 'bumped', txHash: signed.hash };
  }

  /**
   * Resolve every treasury action whose outcome is unknown, from chain truth.
   * Runs at startup and at the top of each tick. Until it is clean, no new
   * treasury transaction is signed: one unresolved nonce in front of the
   * settlement lane is a settlement outage.
   */
  async function recover({ trigger = 'startup' } = {}) {
    const report = { checked: 0, confirmed: 0, failed: 0, rebroadcast: 0, bumped: 0, stillUnknown: 0 };
    const rows = tstore.listUnresolved();
    for (const row of rows) {
      report.checked += 1;
      try {
        if (!row.tx_hash) {
          // Crashed between the intent row and the signature: nothing was
          // ever broadcast, so nothing moved.
          tstore.fail(row.action_id, 'crash_before_broadcast');
          report.failed += 1;
          continue;
        }
        let receipt = null;
        try {
          receipt = await rpc.call('eth_getTransactionReceipt', [row.tx_hash]);
        } catch (e) { receipt = null; }
        if (receipt) {
          const r = settleRowFromReceipt(row, receipt);
          if (r.outcome === 'confirmed') report.confirmed += 1; else report.failed += 1;
          continue;
        }
        // No receipt. Is it still in a mempool?
        let pending = null;
        try {
          pending = await rpc.call('eth_getTransactionByHash', [row.tx_hash]);
        } catch (e) { pending = null; }
        if (pending === null) {
          if (!row.raw_tx) {
            tstore.fail(row.action_id, 'vanished without stored raw tx');
            report.failed += 1;
            continue;
          }
          try {
            await rpc.call('eth_sendRawTransaction', [row.raw_tx]);
            if (row.tx_nonce !== null && row.tx_nonce !== undefined) nonceLane.commit(Number(row.tx_nonce));
            report.rebroadcast += 1;
            logger.warn('treasury_rebroadcast', { action_id: row.action_id, kind: row.kind, tx: row.tx_hash });
          } catch (e) {
            if (/nonce too low|already known|already imported/i.test(String(e.message))) {
              // Someone (probably us, pre-crash) already used that nonce.
              // The next pass reads the receipt; if it never appears the row
              // is failed below by the age guard.
              report.stillUnknown += 1;
            } else {
              tstore.fail(row.action_id, `rebroadcast_failed: ${e.message}`);
              report.failed += 1;
            }
          }
          continue;
        }
        // Still pending: bump it once it has been stuck long enough.
        const ageS = nowSec() - Number(row.created_at || 0);
        if (ageS >= t.stuckTxS) {
          const b = await bumpStuck(row);
          if (b.outcome === 'bumped') { report.bumped += 1; continue; }
          report.stillUnknown += 1;
          logger.error('treasury_tx_stuck', {
            action_id: row.action_id, kind: row.kind, tx: row.tx_hash, tx_nonce: row.tx_nonce,
            age_s: ageS, detail: b.detail,
            remedy: 'replace this nonce manually (same nonce, higher fee) — settlements queue behind it',
          });
        } else {
          report.stillUnknown += 1;
        }
      } catch (e) {
        report.stillUnknown += 1;
        logger.error('treasury_recover_error', { action_id: row.action_id, detail: e.message });
      }
    }
    publishUnresolved();
    if (report.checked) logger.info('treasury_recovery', { trigger, ...report });
    return report;
  }

  /* ------------------------------------------------------------------ sip -- */

  /**
   * Price sanity for one candidate size. Returns { ok, quote, minOut } or a
   * skip payload. Every check is read-only and free, so they all run before
   * any row is written or any gas is spent.
   */
  async function priceForAmount(amountIn, fees, { needsApprove }) {
    const quote = await bestQuote(amountIn);
    if (!quote) return { ok: false, skip: skipped('no_quote') };
    const minOut = (quote.amountOut * (10_000n - BigInt(t.maxSlippageBps))) / 10_000n;
    if (minOut <= 0n) return { ok: false, skip: skipped('quote_dust', { quote_wei: quote.amountOut.toString() }) };

    // The slippage bound is derived from the very pool it protects, so a
    // manipulated/thin/stale quote drags amountOutMinimum down with it. Two
    // optional independent references catch that:
    //   1. X402_ETH_USD_PRICE (the knob the settlement path already has),
    //   2. the realised rate of our own last confirmed sip.
    if (cfg.ethUsdPrice && cfg.ethUsdPrice > 0n) {
      const referenceWei = (amountIn * 1_000_000_000_000n) / cfg.ethUsdPrice;
      const floorWei = (referenceWei * (10_000n - BigInt(t.maxQuoteDeviationBps))) / 10_000n;
      if (quote.amountOut < floorWei) {
        return {
          ok: false,
          skip: skipped('quote_off_reference', {
            quote_wei: quote.amountOut.toString(),
            reference_wei: referenceWei.toString(),
            max_deviation_bps: t.maxQuoteDeviationBps,
            detail: 'the pool quotes far below X402_ETH_USD_PRICE — refusing to convert revenue at that rate',
          }),
        };
      }
    }
    const last = tstore.lastRealizedSip();
    if (last && nowSec() - last.at <= t.rateReferenceMaxAgeS) {
      const expected = (amountIn * last.ethWei) / last.usdcAtomic;
      const floorWei = (expected * (10_000n - BigInt(t.maxQuoteDeviationBps))) / 10_000n;
      if (quote.amountOut < floorWei) {
        return {
          ok: false,
          skip: skipped('quote_off_last_realized', {
            quote_wei: quote.amountOut.toString(),
            expected_wei: expected.toString(),
            max_deviation_bps: t.maxQuoteDeviationBps,
            detail: 'the quote is far below the rate our own last sip actually filled at',
          }),
        };
      }
    }

    // Economic sanity, priced on what THIS attempt really costs: measured gas
    // for the legs it will actually send (the approve is skipped when an
    // allowance already covers the swap), at the fee the transaction actually
    // pays (baseFee + tip), not at the 2*baseFee ceiling.
    const gasUnits = MEASURED_GAS.swap + (needsApprove ? MEASURED_GAS.approve : 0n);
    const effectivePriceWei = (fees.baseFee || 0n) + fees.maxPriorityFeePerGas;
    const costWei = gasUnits * (effectivePriceWei > 0n ? effectivePriceWei : fees.maxFeePerGas);
    if (minOut < costWei * BigInt(t.minEthOutGasRatio)) {
      return {
        ok: false,
        skip: skipped('uneconomic', {
          min_out_wei: minOut.toString(),
          gas_cost_wei: costWei.toString(),
          gas_units: gasUnits.toString(),
          required_ratio: t.minEthOutGasRatio,
        }),
      };
    }
    return { ok: true, quote, minOut, costWei };
  }

  /**
   * One adaptive sip attempt. Never throws: every outcome is a report the
   * caller can log. `force` (manual CLI sip) bypasses the ETH floor, the
   * cooldown and a previous disable — it does NOT bypass the daily budget,
   * the minimum size, the slippage bound, the price references or the
   * economic sanity check, because those bound how much money a mistake can
   * cost. Balances are always read from the chain here: no caller may supply
   * the number that sizes a real transfer.
   */
  async function attemptSip({ trigger = 'interval', force = false } = {}) {
    if (!t.enabled) return skipped('treasury_disabled');
    if (leaseRequired && !leaseHeld) return skipped('lease_unavailable', { detail: 'another process holds the treasury signing lease' });
    if (sippingDisabled && !force) return skipped('sipping_disabled', { detail: sippingDisabledReason });
    const unresolved = tstore.unresolvedCount();
    if (unresolved > 0) {
      return skipped('unresolved_action', {
        unresolved,
        detail: 'a previous treasury transaction has not been resolved on-chain; signing another would queue behind its nonce',
      });
    }

    const { ethWei, usdcAtomic } = await readBalances();

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
    // whatever fires the trigger (interval, settlement burst, or both). This
    // read only SIZES the quote; the authoritative check is the atomic
    // beginSip() below, which writes the intent row in the same DB
    // transaction so a second process cannot get a second full allocation.
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

    let allowance;
    try {
      allowance = uniswap.decodeUint(
        await rpc.call('eth_call', [{ to: USDC, data: uniswap.allowanceCalldata(ME, ROUTER) }, 'latest'])
      );
    } catch (e) {
      return skipped('allowance_read_failed', { detail: e.message });
    }

    let fees;
    try {
      fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
    } catch (e) {
      return skipped('fee_estimate_failed', { detail: e.message });
    }

    let priced = await priceForAmount(amountIn, fees, { needsApprove: allowance < amountIn });
    if (!priced.ok) return priced.skip;

    // The treasury spends the facilitator's ETH, so it answers to the same
    // daily ceiling settlements do — and must stop when that breaker opens.
    // Reserved at MEASURED gas (the caps are 2x the real cost and would eat
    // the whole daily budget in one attempt at an elevated fee) but at the
    // fee CEILING, since a reservation should be an upper bound on price.
    const worstReserve = (MEASURED_GAS.swap + (allowance < amountIn ? MEASURED_GAS.approve : 0n)) * fees.maxFeePerGas;
    const budgetSkip = gasBudgetSkip(worstReserve, 'sip');
    if (budgetSkip) return budgetSkip;

    // ---- claim the daily swap budget and write the intent row BEFORE any
    // gas is spent (read-then-act was worth a second full allocation to a
    // concurrent CLI run).
    const claim = tstore.beginSip({
      trigger,
      amount: amountIn,
      minAmount: t.sipMinUsdcAtomic,
      budget: t.dailySwapBudgetAtomic,
      windowSec: DAY_SECONDS,
      quoteWei: priced.quote.amountOut,
      minOutWei: priced.minOut,
      poolFee: priced.quote.fee,
      destination: ROUTER,
    });
    if (!claim.ok) {
      return skipped(claim.reason, {
        spent_today_usdc: usd(claim.spent || 0n),
        budget_usdc: usd(t.dailySwapBudgetAtomic),
      });
    }
    const actionId = claim.actionId;
    if (claim.amount !== amountIn) {
      // Another writer took budget between the sizing read and the claim.
      amountIn = claim.amount;
      priced = await priceForAmount(amountIn, fees, { needsApprove: allowance < amountIn });
      if (!priced.ok) {
        tstore.fail(actionId, `budget_raced: ${priced.skip.reason}`);
        return priced.skip;
      }
      tstore.setAmount(actionId, amountIn);
    }
    const { quote, minOut } = priced;

    // ---- allowance (exact amount, per sip: no standing approval survives)
    let approvedHere = false;
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
        approvedHere = true;
        // Confirm the allowance really landed before spending gas on a swap
        // that would otherwise revert with STF.
        const after = uniswap.decodeUint(
          await rpc.call('eth_call', [{ to: USDC, data: uniswap.allowanceCalldata(ME, ROUTER) }, 'latest'])
        );
        if (after < amountIn) {
          tstore.fail(approveId, `allowance still ${after} after approve`);
          tstore.fail(actionId, 'approve ineffective');
          recordFailure('sip', 'allowance', { hardDisable: false });
          logger.error('treasury_approve_ineffective', { action_id: approveId, allowance: after.toString(), needed: amountIn.toString() });
          return { action: 'failed', reason: 'allowance', detail: 'approve confirmed but allowance did not increase' };
        }
      } catch (e) {
        const cls = e.classification || { class: 'unknown', strike: true, hardDisable: false };
        if (e.unknownOutcome) {
          // The approve may have landed; leave the row 'submitting' so
          // recover() resolves it from chain truth on the next tick, and
          // never broadcast the swap that would then STF.
          tstore.fail(actionId, `approve_unknown: ${e.message}`);
          logger.warn('treasury_approve_unknown_outcome', { action_id: approveId, tx: e.txHash, stage: e.stage, detail: e.message });
          return { action: 'unknown', reason: `approve_${cls.class}`, action_id: approveId, detail: e.message };
        }
        tstore.fail(approveId, e.message, { gasSpentWei: e.gasSpentWei, txHash: e.txHash });
        tstore.fail(actionId, `approve_${cls.class}`);
        recordFailure('sip', `approve_${cls.class}`, cls);
        logger.error('treasury_approve_failed', { action_id: approveId, stage: e.stage, class: cls.class, detail: e.message });
        return { action: 'failed', reason: `approve_${cls.class}`, detail: e.message };
      }
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

    let res;
    try {
      res = await sendTx({ actionId, to: ROUTER, data, gasLimitMax: t.maxSwapGas, fees, label: 'sip' });
    } catch (e) {
      const cls = e.classification || { class: 'unknown', strike: true, hardDisable: false };
      if (e.unknownOutcome) {
        // The tx may have landed. Leave the row 'submitting' so the daily
        // budget keeps counting it and recover() resolves it from the chain
        // on the next tick; do NOT strike.
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
      // The approve landed and the swap did not: leave no standing allowance
      // on a hot wallet holding live revenue.
      if (approvedHere) await revokeAllowance({ fees, trigger });
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
      if (approvedHere) await revokeAllowance({ fees, trigger });
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

  /**
   * Set the router allowance back to zero after a failed swap. Best effort
   * and never a strike: the sip already failed, and a leftover exact-amount
   * allowance is a small standing risk we clear rather than an emergency.
   */
  async function revokeAllowance({ fees, trigger = 'interval' }) {
    try {
      const allowance = uniswap.decodeUint(
        await rpc.call('eth_call', [{ to: USDC, data: uniswap.allowanceCalldata(ME, ROUTER) }, 'latest'])
      );
      if (allowance <= 0n) return null;
      const reserve = MEASURED_GAS.approve * fees.maxFeePerGas;
      if (gasBudgetSkip(reserve, 'approve_revoke')) return null;
      const actionId = tstore.begin({ kind: 'approve', trigger, usdcAmount: 0n, destination: ROUTER });
      const res = await sendTx({
        actionId,
        to: USDC,
        data: uniswap.approveCalldata(ROUTER, 0n),
        gasLimitMax: t.maxApproveGas,
        fees,
        label: 'approve_revoke',
      });
      tstore.confirm(actionId, { gasSpentWei: res.gasSpentWei, txHash: res.txHash });
      logger.info('treasury_allowance_revoked', { action_id: actionId, tx: res.txHash, spender: ROUTER });
      return { action: 'revoked', tx: res.txHash };
    } catch (e) {
      logger.warn('treasury_allowance_revoke_failed', {
        detail: e.message,
        remedy: 'the router allowance is exact-amount, so the exposure is one sip; clear it manually if it persists',
      });
      return null;
    }
  }

  /* ---------------------------------------------------------------- sweep -- */

  /**
   * Drain USDC above the ceiling to the cold address. The destination is the
   * construction-time COLD binding and nothing else: this function takes no
   * destination parameter, and adding one would be the bug the immutability
   * test exists to catch. The AMOUNT is equally uncontrollable from outside —
   * it comes from a balance this function reads itself.
   */
  async function attemptSweep({ trigger = 'interval', force = false } = {}) {
    if (!t.enabled) return skipped('treasury_disabled');
    if (leaseRequired && !leaseHeld) return skipped('lease_unavailable', { detail: 'another process holds the treasury signing lease' });
    if (!COLD) return skipped('no_cold_address');
    if (sweepingDisabled && !force) return skipped('sweeping_disabled', { detail: sweepingDisabledReason });
    const unresolved = tstore.unresolvedCount();
    if (unresolved > 0) {
      return skipped('unresolved_action', {
        unresolved,
        detail: 'a previous treasury transaction has not been resolved on-chain; signing another would queue behind its nonce',
      });
    }

    const cold = await verifyColdAddress();
    if (!cold.ok) return skipped(cold.reason, { detail: cold.detail, cold_address: COLD });

    const { usdcAtomic } = await readBalances();
    if (usdcAtomic <= t.usdcCeilingAtomic) {
      return skipped('below_ceiling', { usdc: usd(usdcAtomic), ceiling_usdc: usd(t.usdcCeilingAtomic) });
    }
    const surplus = usdcAtomic - t.usdcCeilingAtomic;
    if (!force && surplus < t.minSweepAtomic) {
      return skipped('below_min_sweep', { surplus_usdc: usd(surplus), min_sweep_usdc: usd(t.minSweepAtomic) });
    }

    const sweepsToday = tstore.sweepCountSince(nowSec() - DAY_SECONDS);
    if (!force && sweepsToday >= t.maxSweepsPerDay) {
      return skipped('sweep_budget_exhausted', { sweeps_today: sweepsToday, max_per_day: t.maxSweepsPerDay });
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

    const budgetSkip = gasBudgetSkip(MEASURED_GAS.sweep * fees.maxFeePerGas, 'sweep');
    if (budgetSkip) return budgetSkip;

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

  /**
   * Track "the wallet is under the floor and the sip did not run". A skip is
   * the safe direction, but an operator who is never told cannot act — and
   * the end state of a silent skip loop is an empty wallet and a facilitator
   * that stops taking payments.
   */
  function noteRefuelOutcome(balances, sip) {
    const underFloor = balances.ethWei !== null && balances.ethWei < t.ethFloorWei;
    const blocked = underFloor && sip && sip.action === 'skipped' && REFUEL_BLOCKED_REASONS.has(sip.reason);
    if (blocked) {
      refuelBlockedStreak += 1;
      refuelBlockedReason = sip.reason;
      if (refuelBlockedStreak === t.refuelAlertTicks) {
        logger.error('treasury_refuel_blocked', {
          consecutive_ticks: refuelBlockedStreak,
          reason: refuelBlockedReason,
          eth_wei: balances.ethWei.toString(),
          floor_wei: t.ethFloorWei.toString(),
          remedy: 'the wallet cannot buy its own gas back — top it up manually and check `animica-x402 treasury status`',
        });
      }
    } else if (!underFloor || (sip && (sip.action === 'sipped' || sip.action === 'unknown'))) {
      refuelBlockedStreak = 0;
      refuelBlockedReason = null;
    }
    if (metrics && metrics.treasuryRefuelBlocked) {
      metrics.treasuryRefuelBlocked.set({}, refuelBlockedStreak >= t.refuelAlertTicks ? 1 : 0);
    }
  }

  /** One full check. Never throws — the caller is a timer or a hook. */
  async function tick({ trigger = 'interval' } = {}) {
    const report = {
      trigger, at: now(), eth_wei: null, usdc_atomic: null,
      recovery: null, sip: null, sweep: null, error: null,
    };
    try {
      // A service that lost the lease (CLI took it, or the row expired while
      // the process was stopped) reclaims it here; until it does, both
      // attempts skip rather than sign onto a lane someone else owns.
      if (leaseRequired && !leaseHeld) acquireLease({ label: 'facilitator' });
      else renewLease();
      // Chain truth first: an unresolved treasury transaction blocks the
      // settlement lane, so resolving it outranks anything else here.
      if (tstore.unresolvedCount() > 0) report.recovery = await recover({ trigger });
      const balances = await readBalances();
      report.eth_wei = balances.ethWei.toString();
      report.usdc_atomic = balances.usdcAtomic.toString();
      report.sip = await attemptSip({ trigger });
      noteRefuelOutcome(balances, report.sip);
      // attemptSweep re-reads the balance itself, so a sip that moved money
      // (including one whose receipt never came back) can never leave the
      // sweep sizing itself from a stale number.
      report.sweep = await attemptSweep({ trigger });
      publishUnresolved();
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

  /* ----------------------------------------------------------- the lease -- */

  /**
   * Exactly one process may sign treasury transactions against a given DB.
   * The service takes the lease at start() and renews it every tick; the CLI
   * takes it for the duration of one command and refuses when the service
   * holds it. Two signers on one nonce lane is how a manual sip steals a live
   * settlement's nonce.
   */
  function acquireLease({ label = 'facilitator', ttlS = t.leaseTtlS } = {}) {
    const r = tstore.acquireLease(leaseOwner, ttlS, label);
    leaseHeld = Boolean(r.ok);
    return r;
  }

  function renewLease() {
    if (!leaseHeld) return false;
    const r = tstore.acquireLease(leaseOwner, t.leaseTtlS, 'facilitator');
    leaseHeld = Boolean(r.ok);
    if (!r.ok) {
      logger.error('treasury_lease_lost', {
        holder: r.holder && r.holder.label,
        detail: 'another process holds the treasury lease; this one will not sign until it is free',
      });
    }
    return leaseHeld;
  }

  function releaseLease() {
    if (!leaseHeld) return false;
    leaseHeld = false;
    return tstore.releaseLease(leaseOwner);
  }

  function start() {
    if (!t.enabled || timer) return;
    leaseRequired = true;
    const lease = acquireLease({ label: 'facilitator' });
    if (!lease.ok) {
      logger.error('treasury_lease_busy', {
        holder: lease.holder,
        detail: 'another process holds the treasury lease (a CLI sip/sweep?); starting anyway but not signing until it expires',
      });
    }
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
      lease: lease.ok,
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
    leaseRequired = false;
    releaseLease();
  }

  /** Non-fatal condition for /readyz. Null when everything is nominal. */
  function warning() {
    if (!t.enabled) return null;
    const parts = [];
    if (sippingDisabled) parts.push(sippingDisabledReason || 'sipping disabled');
    if (sweepingDisabled) parts.push(sweepingDisabledReason || 'sweeping disabled');
    if (refuelBlockedStreak >= t.refuelAlertTicks) {
      parts.push(
        `cannot refuel: ${refuelBlockedStreak} consecutive checks under the ETH floor with the sip skipped `
        + `(${refuelBlockedReason}) — top the wallet up manually`
      );
    }
    const unresolved = tstore.unresolvedCount();
    if (unresolved > 0) {
      parts.push(
        `${unresolved} treasury transaction(s) unresolved on-chain — settlements queue behind their nonce until they clear`
      );
    }
    if (coldCheck && !coldCheck.ok && coldCheck.reason === 'cold_address_is_contract') {
      parts.push(coldCheck.detail);
    }
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
      cold_address_checked: coldCheck ? coldCheck.ok : null,
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
        max_quote_deviation_bps: t.maxQuoteDeviationBps,
        min_eth_out_gas_ratio: t.minEthOutGasRatio,
        max_sweeps_per_day: t.maxSweepsPerDay,
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
      refuel_blocked_ticks: refuelBlockedStreak,
      refuel_blocked_reason: refuelBlockedReason,
      unresolved_actions: tstore.unresolvedCount(),
      lease_held: leaseHeld,
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
    refuelBlockedStreak = 0;
    refuelBlockedReason = null;
    coldCheck = null;
    persistSipState();
    persistSweepState();
    if (metrics) {
      metrics.treasurySippingEnabled.set({}, 1);
      metrics.treasurySweepingEnabled.set({}, 1);
      if (metrics.treasuryRefuelBlocked) metrics.treasuryRefuelBlocked.set({}, 0);
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
    recover,
    notifySettlement,
    // The public entry points refresh the unresolved-actions gauge on the way
    // out, so a direct attemptSip()/sipNow() (the CLI, a test) leaves the same
    // observable state a full tick would.
    sipNow: (opts = {}) => withGauge(() => attemptSip({ trigger: 'manual', ...opts, force: true })),
    sweepNow: (opts = {}) => withGauge(() => attemptSweep({ trigger: 'manual', ...opts, force: true })),
    attemptSip: (opts = {}) => withGauge(() => attemptSip(opts)),
    attemptSweep: (opts = {}) => withGauge(() => attemptSweep(opts)),
    readBalances,
    tokenHealth,
    bestQuote,
    verifyColdAddress,
    acquireLease,
    releaseLease,
    leaseOwner,
    nonces: nonceLane,
    status,
    warning,
    resume,
  };
}

module.exports = { createTreasury, DAY_SECONDS, MEASURED_GAS };
