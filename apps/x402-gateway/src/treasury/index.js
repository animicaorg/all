'use strict';
/**
 * Treasury module entry point + the startup policy gate for single-wallet
 * mode.
 *
 * SINGLE-WALLET MODE is the operator's chosen deployment: payTo
 * (X402_SETTLEMENT_ADDRESS) IS the facilitator's own address, so USDC revenue
 * lands on the hot wallet that pays gas. That is a deliberate trade — it buys
 * a self-refuelling loop that needs one ~$2 ETH funding and no operator
 * attention, and it costs the separation between "gas float" and "revenue".
 * The compensating control is this module: revenue above the ceiling is
 * continuously drained to a cold address the hot key cannot change.
 *
 * Without that control the trade is simply "all revenue sits on a hot key",
 * which is not a trade at all. So the gate below refuses to start in
 * single-wallet mode unless the treasury is enabled AND a cold address is
 * configured. It is enforced where the facilitator address is actually known
 * (facilitator construction), not in the env parser, because deriving the
 * address requires the private key and key material never leaves key.js.
 */

const evm = require('../facilitator-evm/evm');
const { createTreasury, DAY_SECONDS } = require('./treasury');
const { createTreasuryStore } = require('./store');
const uniswap = require('./uniswap');

/**
 * @returns {{singleWallet: boolean, treasuryRequired: boolean}}
 * @throws when the configuration is a combination we refuse to serve.
 */
function assertTreasuryPolicy(cfg, facilitatorAddress) {
  const t = cfg.treasury || { enabled: false };
  const singleWallet = evm.addressEquals(cfg.settlementAddress, facilitatorAddress);

  if (singleWallet) {
    if (!t.enabled) {
      throw new Error(
        `single-wallet mode refused: X402_SETTLEMENT_ADDRESS (${cfg.settlementAddress}) is the facilitator's own address, `
        + 'so every USDC payment lands on the hot key that signs settlements. That is only acceptable with the treasury '
        + 'module draining it: set X402_TREASURY_ENABLED=1 and X402_TREASURY_COLD_ADDRESS=<checksummed cold wallet>, '
        + 'or point X402_SETTLEMENT_ADDRESS at a wallet the facilitator does not control.'
      );
    }
    if (!t.coldAddress) {
      throw new Error(
        'single-wallet mode refused: X402_TREASURY_ENABLED=1 without X402_TREASURY_COLD_ADDRESS would accumulate every '
        + 'dollar of revenue on the hot facilitator key with nowhere to sweep it.'
      );
    }
    if (evm.addressEquals(t.coldAddress, facilitatorAddress)) {
      throw new Error(
        'single-wallet mode refused: X402_TREASURY_COLD_ADDRESS equals the facilitator address, so the sweep would move '
        + 'money from the hot wallet to itself and drain nothing.'
      );
    }
  }

  if (t.enabled && !uniswap.CONTRACTS[cfg.network]) {
    throw new Error(
      `X402_TREASURY_ENABLED=1 on network ${cfg.network}, which has no live-verified Uniswap v3 contract set `
      + `(supported: ${Object.keys(uniswap.CONTRACTS).join(', ')})`
    );
  }

  return { singleWallet, treasuryRequired: singleWallet };
}

module.exports = { createTreasury, createTreasuryStore, assertTreasuryPolicy, uniswap, DAY_SECONDS };
