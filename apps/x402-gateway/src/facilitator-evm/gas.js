'use strict';
/**
 * Gas sponsorship policy. All quantities BigInt wei / gas units.
 *
 *   - fee estimation: maxPriorityFeePerGas = max(rpc suggestion, 1e6 wei);
 *     maxFeePerGas = 2 * baseFee(latest) + priority — both clamped by the
 *     configured cap (X402_MAX_FEE_PER_GAS_WEI). If the clamp would put
 *     maxFeePerGas BELOW the current base fee the tx cannot be included at
 *     an acceptable price: reject (fee_cap_exceeded), do not underbid and
 *     strand a nonce.
 *   - gas limit: eth_estimateGas on the exact settlement calldata (doubles
 *     as the authoritative simulation — a consumed nonce / bad signature /
 *     blacklisted payer reverts here), * 1.25 buffer, clamped by
 *     X402_MAX_GAS_PER_SETTLEMENT. Estimate above the cap => pathological
 *     tx => reject.
 *   - optional daily budget (X402_DAILY_GAS_BUDGET_WEI): sum of recorded
 *     spend over the trailing 24 h from the store; at/over budget the
 *     circuit breaker opens and settlement refuses BEFORE claiming the
 *     authorization (payer keeps a usable authorization; we stop bleeding).
 */

const { quantityToBigInt } = require('./evm');

const MIN_PRIORITY_FEE_WEI = 1_000_000n; // 0.001 gwei, observed Base floor
const GAS_BUFFER_NUM = 125n;
const GAS_BUFFER_DEN = 100n;

class GasPolicyError extends Error {
  constructor(reason, detail) {
    super(detail || reason);
    this.reason = reason;
  }
}

/**
 * Compute fee fields for a settlement tx. `rpc` is the JSON-RPC client.
 * Returns { maxFeePerGas, maxPriorityFeePerGas, baseFee }.
 */
async function estimateFees(rpc, { maxFeePerGasCap }) {
  let priority = MIN_PRIORITY_FEE_WEI;
  try {
    const suggested = quantityToBigInt(await rpc.call('eth_maxPriorityFeePerGas', []));
    if (suggested > priority) priority = suggested;
  } catch (e) {
    // Suggestion endpoint down: keep the floor — base-fee math still bounds us.
  }
  const block = await rpc.call('eth_getBlockByNumber', ['latest', false]);
  if (!block || block.baseFeePerGas === undefined) {
    throw new GasPolicyError('unexpected_settle_error', 'latest block has no baseFeePerGas');
  }
  const baseFee = quantityToBigInt(block.baseFeePerGas);
  let maxFee = 2n * baseFee + priority;
  if (maxFee > maxFeePerGasCap) {
    if (maxFeePerGasCap < baseFee) {
      throw new GasPolicyError(
        'fee_cap_exceeded',
        `current base fee ${baseFee} wei exceeds X402_MAX_FEE_PER_GAS_WEI ${maxFeePerGasCap}`
      );
    }
    maxFee = maxFeePerGasCap;
    if (priority > maxFee) priority = maxFee;
  }
  return { maxFeePerGas: maxFee, maxPriorityFeePerGas: priority, baseFee };
}

/**
 * Estimate gas for the exact settlement calldata (this IS the simulation
 * check) and apply buffer + cap. Throws GasPolicyError on revert or cap
 * breach. Returns { gasLimit, estimated }.
 */
async function estimateGasLimit(rpc, { from, to, data, maxGasPerSettlement }) {
  let estimated;
  try {
    estimated = quantityToBigInt(await rpc.call('eth_estimateGas', [{ from, to, data }]));
  } catch (e) {
    // Revert here = the transfer cannot succeed (consumed nonce, bad sig,
    // paused token, blacklist ...). Surfaced as a verification-grade error.
    throw new GasPolicyError('simulation_reverted', `eth_estimateGas rejected settlement: ${e.message}`);
  }
  if (estimated > maxGasPerSettlement) {
    throw new GasPolicyError(
      'gas_limit_exceeded',
      `estimated gas ${estimated} exceeds X402_MAX_GAS_PER_SETTLEMENT ${maxGasPerSettlement}`
    );
  }
  let gasLimit = (estimated * GAS_BUFFER_NUM) / GAS_BUFFER_DEN;
  if (gasLimit > maxGasPerSettlement) gasLimit = maxGasPerSettlement;
  return { gasLimit, estimated };
}

/**
 * Daily budget circuit breaker. Returns null when within budget, or a
 * GasPolicyError to throw. Budget 0n/undefined disables the breaker.
 */
/**
 * Daily circuit breaker.
 *
 * `reserveWei` is the worst-case cost of the settlement about to be signed.
 * Recorded spend alone is NOT a safe basis: gas lands in gas_spend only after
 * the receipt, tens of seconds later, so N concurrent settles would each see
 * the same stale total and collectively blow through the cap. Counting
 * in-flight reservations (+ this one) closes that race, which is why the
 * authoritative call happens inside the submit lock.
 */
function checkDailyBudget(store, dailyBudgetWei, { reserveWei = 0n, now = Date.now } = {}) {
  if (!dailyBudgetWei || dailyBudgetWei <= 0n) return null;
  const since = Math.floor(now() / 1000) - 86_400;
  const spent = store.gasSpentSince(since);
  // Treasury transactions (approve/sip/sweep) spend from the SAME account.
  // Leaving them out made the documented "daily ETH ceiling" bound only half
  // the outflow, and let the treasury keep spending while the breaker was open.
  const treasurySpent = typeof store.treasuryGasSpentSince === 'function'
    ? store.treasuryGasSpentSince(since) : 0n;
  const reservedInFlight = typeof store.gasReservedInFlight === 'function'
    ? store.gasReservedInFlight() : 0n;
  const projected = spent + treasurySpent + reservedInFlight + BigInt(reserveWei || 0n);
  if (projected >= dailyBudgetWei) {
    return new GasPolicyError(
      'gas_budget_exhausted',
      `projected trailing-24h gas ${projected} wei (settlements ${spent} + treasury ${treasurySpent}` +
      ` + in-flight ${reservedInFlight}${reserveWei ? ` + this ${reserveWei}` : ''})` +
      ` >= X402_DAILY_GAS_BUDGET_WEI ${dailyBudgetWei}`
    );
  }
  return null;
}

/**
 * Economic floor: refuse to sponsor gas that costs more than the payment is
 * worth. Optional — only enforced when the operator sets an ETH/USD price,
 * because comparing wei to USDC atomic units needs one. USDC has 6 decimals.
 */
function checkEconomicFloor({ usdcAtomic, reserveWei, ethUsdPrice, safetyMultiple = 2 }) {
  if (!ethUsdPrice || ethUsdPrice <= 0n) return null;
  const value = BigInt(usdcAtomic || 0n);
  if (value <= 0n) return null;
  // gas cost in USDC atomic units = reserveWei * ethUsdPrice * 1e6 / 1e18
  const gasCostUsdcAtomic = (BigInt(reserveWei || 0n) * BigInt(ethUsdPrice)) / 1_000_000_000_000n;
  if (gasCostUsdcAtomic * BigInt(safetyMultiple) > value) {
    return new GasPolicyError(
      'gas_exceeds_payment_value',
      `sponsored gas ~${gasCostUsdcAtomic} USDC atomic x${safetyMultiple} exceeds the ${value} collected`
    );
  }
  return null;
}

/** gasUsed * effectiveGasPrice + OP-stack l1Fee (both from the receipt). */
function receiptGasSpentWei(receipt) {
  let spent = 0n;
  if (receipt.gasUsed && receipt.effectiveGasPrice) {
    spent += quantityToBigInt(receipt.gasUsed) * quantityToBigInt(receipt.effectiveGasPrice);
  }
  if (receipt.l1Fee) {
    try {
      spent += quantityToBigInt(receipt.l1Fee);
    } catch (e) { /* absent/odd shape on non-OP chains */ }
  }
  return spent;
}

module.exports = {
  MIN_PRIORITY_FEE_WEI,
  GasPolicyError,
  estimateFees,
  estimateGasLimit,
  checkDailyBudget,
  checkEconomicFloor,
  receiptGasSpentWei,
};
