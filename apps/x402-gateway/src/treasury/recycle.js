'use strict';
/**
 * TREASURY RECYCLER - turn x402 USDC revenue into ANM.
 *
 * THE LOOP the operator asked for:
 *   agents pay USDC (Base)  ->  deposit USDC to NonKYC  ->  auto-buy ANM
 *   ->  operator MANUALLY withdraws ANM to the treasury.
 *
 * The last step stays manual on purpose: an automated exchange withdrawal is
 * the single highest-value thing an attacker could turn against us, and the
 * operator explicitly wants to keep it in hand.
 *
 * WHY THIS FILE IS SO CAUTIOUS. It moves real money on an irreversible rail,
 * and the specific way it can lose everything is a WRONG-NETWORK DEPOSIT:
 * EVM addresses are identical across chains, so sending Base USDC to a
 * BEP20 deposit address confirms successfully on Base and is simply never
 * credited. There is no error, no bounce and usually no recovery. The
 * existing /etc/animica/compute-broker.env carries
 * NONKYC_USDC_NETWORK=BEP20 for this very address, so this is a live
 * hazard on this box, not a hypothetical one.
 *
 * Therefore:
 *   - the deposit network is REQUIRED config and must match the chain we
 *     are sending from; there is no default and no inference;
 *   - the first deposit is capped to a test amount and the operator must
 *     confirm it was credited before larger amounts are allowed;
 *   - dry-run is the DEFAULT. Nothing moves without an explicit --apply;
 *   - this module never uses the facilitator's key. It takes its own, and
 *     with no key configured it can only ever plan and report.
 */

const CHAIN_TO_NETWORK_LABEL = {
  8453: 'BASE',
  1: 'ERC20',
  56: 'BEP20',
  137: 'POLYGON',
};

function createRecycler({ cfg, fetchImpl = fetch, evmRpc, logger = null, now = Date.now }) {
  const log = logger || { info() {}, warn() {}, error() {} };

  /** USDC balance of an address, in atomic units (6dp), read-only. */
  async function usdcBalance(address) {
    // balanceOf(address)
    const data = '0x70a08231' + address.replace(/^0x/, '').toLowerCase().padStart(64, '0');
    const hex = await evmRpc('eth_call', [{ to: cfg.usdcAsset, data }, 'latest']);
    return BigInt(hex === '0x' ? '0x0' : hex);
  }

  function atomicToUsd(atomic) {
    const v = BigInt(atomic);
    return `${v / 1000000n}.${(v % 1000000n).toString().padStart(6, '0')}`;
  }

  /**
   * THE GUARD THAT MATTERS. Refuse unless the configured deposit network
   * matches the chain we would actually be sending on. Mismatch here is
   * unrecoverable loss, so it is checked before anything else and cannot be
   * overridden by a flag.
   */
  function checkNetwork() {
    const expected = CHAIN_TO_NETWORK_LABEL[Number(cfg.recycleChainId)] || null;
    const configured = String(cfg.recycleDepositNetwork || '').toUpperCase();
    if (!configured) {
      return {
        ok: false,
        reason: 'deposit_network_unset',
        detail:
          'X402_RECYCLE_DEPOSIT_NETWORK is not set. It must name the network the exchange will credit the deposit on (e.g. BASE). There is deliberately no default: guessing this wrong destroys the funds.',
      };
    }
    if (!expected) {
      return {
        ok: false,
        reason: 'unknown_source_chain',
        detail: `no known deposit-network label for chainId ${cfg.recycleChainId}`,
      };
    }
    if (configured !== expected) {
      return {
        ok: false,
        reason: 'network_mismatch',
        detail:
          `REFUSING TO SEND. We would transfer on chainId ${cfg.recycleChainId} (${expected}), but the configured exchange deposit network is ${configured}. ` +
          'EVM addresses are identical across chains, so this transfer would CONFIRM and then never be credited — an unrecoverable loss. ' +
          'Get a deposit address for the correct network from the exchange, or bridge the funds first.',
        expected,
        configured,
      };
    }
    return { ok: true, network: expected };
  }

  /**
   * What would happen, without doing any of it. Safe to run on a timer.
   */
  async function plan() {
    const net = checkNetwork();
    const balance = await usdcBalance(cfg.recycleSourceAddress);
    const reserve = BigInt(cfg.recycleReserveAtomic);
    const minDeposit = BigInt(cfg.recycleMinDepositAtomic);
    const maxPerRun = BigInt(cfg.recycleMaxPerRunAtomic);

    const spendable = balance > reserve ? balance - reserve : 0n;
    let amount = spendable > maxPerRun ? maxPerRun : spendable;

    const blockers = [];
    if (!net.ok) blockers.push({ code: net.reason, detail: net.detail });
    if (amount < minDeposit) {
      blockers.push({
        code: 'below_minimum_deposit',
        detail: `spendable ${atomicToUsd(spendable)} USDC is under the exchange minimum of ${atomicToUsd(minDeposit)} USDC`,
      });
    }
    if (!cfg.recycleEnabled) blockers.push({ code: 'disabled', detail: 'X402_RECYCLE_ENABLED is not 1' });
    if (!cfg.recycleHasKey) {
      blockers.push({
        code: 'no_signing_key',
        detail: 'X402_RECYCLE_PRIVATE_KEY is unset, so this process can plan but cannot move funds. It deliberately does NOT reuse the facilitator key.',
      });
    }
    // The test-deposit gate: until the operator confirms a first deposit was
    // actually credited, cap every run at the test amount. Confirming an
    // unverifiable deposit path with 1 USDC is far cheaper than with 500.
    if (!cfg.recycleDepositConfirmed) {
      const test = BigInt(cfg.recycleTestAmountAtomic);
      if (amount > test) amount = test;
      blockers.push({
        code: 'awaiting_test_deposit_confirmation',
        detail:
          `The deposit path has not been confirmed yet, so this run is capped at ${atomicToUsd(test)} USDC. ` +
          'Send it, check the exchange credited it, then set X402_RECYCLE_DEPOSIT_CONFIRMED=1 to lift the cap.',
        severity: 'gate',
      });
    }

    return {
      ok: blockers.filter((b) => b.severity !== 'gate').length === 0 && amount >= minDeposit,
      chain_id: Number(cfg.recycleChainId),
      source_address: cfg.recycleSourceAddress,
      deposit_address: cfg.recycleDepositAddress,
      deposit_network: cfg.recycleDepositNetwork,
      network_check: net,
      balance_atomic: balance.toString(),
      balance_usd: atomicToUsd(balance),
      reserve_usd: atomicToUsd(reserve),
      spendable_usd: atomicToUsd(spendable),
      would_deposit_atomic: amount.toString(),
      would_deposit_usd: atomicToUsd(amount),
      would_buy: `${cfg.recycleMarket} market buy with the credited USDC`,
      blockers,
      manual_step:
        'After the buy fills, withdraw the ANM to the treasury yourself. This tool never withdraws from the exchange.',
      generated_at: new Date(now()).toISOString(),
    };
  }

  /**
   * Place a market buy for ANM with USDC already sitting on the exchange.
   * Separate from the deposit on purpose: a deposit needs confirmations, so
   * the buy is a later, independent step that can be retried safely.
   */
  async function buyAnm({ amountUsd, apply = false }) {
    if (!cfg.recycleApiKey || !cfg.recycleApiSecret) {
      return { ok: false, reason: 'no_api_credentials', detail: 'exchange API key/secret are not configured' };
    }
    if (!apply) {
      return { ok: true, dry_run: true, would: `market buy ${cfg.recycleMarket} for ${amountUsd} USDC` };
    }
    const body = {
      symbol: cfg.recycleMarket,
      side: 'buy',
      type: 'market',
      quantity: String(amountUsd),
      userProvidedId: `x402-recycle-${Math.floor(now() / 1000)}`,
    };
    const res = await fetchImpl(`${cfg.recycleApiBase}/createorder`, {
      method: 'POST',
      headers: Object.assign(
        { 'content-type': 'application/json' },
        signHeaders(cfg, body, now)
      ),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
    const text = await res.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { /* keep raw */ }
    if (!res.ok || (parsed && parsed.error)) {
      return { ok: false, reason: 'order_rejected', status: res.status, detail: text.slice(0, 500) };
    }
    log.info('recycle_buy_placed', { market: cfg.recycleMarket, amount_usd: amountUsd });
    return { ok: true, order: parsed || text };
  }

  return { plan, buyAnm, usdcBalance, checkNetwork, atomicToUsd };
}

/**
 * HMAC request signing for the exchange API. Kept in one place so the secret
 * is used in exactly one function and never logged.
 */
function signHeaders(cfg, body, now) {
  const crypto = require('node:crypto');
  const nonce = String(Math.floor(now() / 1000));
  const payload = cfg.recycleApiKey + nonce + JSON.stringify(body);
  const sig = crypto.createHmac('sha256', cfg.recycleApiSecret).update(payload).digest('hex');
  return { 'X-API-KEY': cfg.recycleApiKey, 'X-API-NONCE': nonce, 'X-API-SIGN': sig };
}

module.exports = { createRecycler, CHAIN_TO_NETWORK_LABEL, signHeaders };
