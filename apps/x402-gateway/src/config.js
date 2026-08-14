'use strict';
/**
 * Configuration for the x402 gateway scaffold. Everything comes from the
 * environment with documented names; nothing here holds a real key, mint or
 * treasury address. The real values live with their owning systems:
 *
 *   - WANM_MINT / WANM_TREASURY: the real wANM mint lives with the
 *     solana.animica.org bridge configuration. Never hardcode it here.
 *   - SOLANA_RPC_URL: the project already has a server-side QuikNode endpoint
 *     configured elsewhere (env-injected). Point this at it at deploy time;
 *     never paste the URL into source.
 *   - WANM_FEEPAYER_SECRET: the facilitator's sponsor key. Absent, the
 *     facilitator generates an EPHEMERAL keypair and says so loudly — fine for
 *     the demo, useless for production because the pubkey changes every boot.
 *
 * The master switch is ANM_X402_ENABLED=1. Without it the middleware refuses
 * to gate anything (503, not free passage) and the demo server refuses to
 * start. Nothing in this app is wired into nginx/systemd; it is a reviewed
 * scaffold, not a deployment.
 */

const NETWORKS = {
  // CAIP-2 identifiers per x402 spec v2.
  BASE_MAINNET: 'eip155:8453',
  BASE_SEPOLIA: 'eip155:84532',
  SOLANA_MAINNET: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp',
  SOLANA_DEVNET: 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1',
};

// v1 wire uses short slugs instead of CAIP-2; kept for legacy X-PAYMENT flow.
const V1_NETWORK_SLUGS = {
  [NETWORKS.BASE_MAINNET]: 'base',
  [NETWORKS.BASE_SEPOLIA]: 'base-sepolia',
  [NETWORKS.SOLANA_MAINNET]: 'solana',
  [NETWORKS.SOLANA_DEVNET]: 'solana-devnet',
};

// Well-known USDC contracts, override with X402_USDC_ASSET. These are public
// contract addresses, not secrets — but verify against Circle's published list
// before ever enabling the USDC lane for real.
const USDC_DEFAULTS = {
  [NETWORKS.BASE_MAINNET]: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  [NETWORKS.BASE_SEPOLIA]: '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
};

function env(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

function load(overrides = {}) {
  const networkEvm = env('X402_NETWORK_EVM', NETWORKS.BASE_SEPOLIA);
  const networkSvm = env('X402_NETWORK_SVM', NETWORKS.SOLANA_DEVNET);
  const cfg = {
    enabled: env('ANM_X402_ENABLED', '0') === '1',

    // Where the demo/gated resources say they live (v2 resource.url base).
    resourceBaseUrl: env('X402_RESOURCE_BASE_URL', 'http://127.0.0.1:4656'),
    serviceName: env('X402_SERVICE_NAME', 'Animica'),

    // Lane A: USDC on Base via an external, CDP-compatible facilitator.
    networkEvm,
    usdcAsset: env('X402_USDC_ASSET', USDC_DEFAULTS[networkEvm] || ''),
    basePayTo: env('X402_BASE_PAYTO', ''), // EVM address that receives USDC
    evmFacilitatorUrl: env('X402_EVM_FACILITATOR_URL', 'https://x402.org/facilitator'),
    // mainnet alternatives (operator decision, see README):
    //   https://api.cdp.coinbase.com/platform/v2/x402   (Coinbase CDP)
    //   https://facilitator.payai.network               (PayAI)

    // Lane B: wANM (SPL token) via the LOCAL self-facilitator.
    networkSvm,
    wanmMint: env('WANM_MINT', ''), // real mint: solana.animica.org bridge config
    wanmTreasury: env('WANM_TREASURY', ''), // SPL owner wallet receiving wANM
    wanmDecimals: parseInt(env('WANM_DECIMALS', '9'), 10),
    wanmUsdPrice: env('WANM_USD_PRICE', ''), // USD per 1 wANM, decimal string
    wanmFeePayerPubkey: env('WANM_FEEPAYER_PUBKEY', ''), // sponsor pubkey advertised in extra.feePayer
    wanmFeePayerSecret: env('WANM_FEEPAYER_SECRET', ''), // hex/base58 32-byte ed25519 seed (facilitator only)
    svmFacilitatorUrl: env('X402_SVM_FACILITATOR_URL', 'http://127.0.0.1:4655'),

    // Solana JSON-RPC used by the self-facilitator. Env-injected; the repo's
    // existing QuikNode endpoint qualifies. No default on purpose: a silent
    // fallback to a public RPC is how you end up rate-limited mid-settle.
    solanaRpcUrl: env('SOLANA_RPC_URL', ''),

    facilitatorPort: parseInt(env('X402_FACILITATOR_PORT', '4655'), 10),
    demoPort: parseInt(env('X402_DEMO_PORT', '4656'), 10),
    maxTimeoutSeconds: parseInt(env('X402_MAX_TIMEOUT_SECONDS', '60'), 10),
  };
  return Object.assign(cfg, overrides);
}

/**
 * "0.005" at scale 6 -> 5000n. BigInt end to end; Number would be fine at
 * these magnitudes today and silently wrong the day someone prices in wANM
 * base units. Mirrors bridge/packages/shared/src/decimals.ts.
 */
function decimalToScaled(s, scale) {
  if (typeof s !== 'string' || !/^\d+(\.\d+)?$/.test(s)) {
    throw new Error(`invalid decimal string: ${JSON.stringify(s)}`);
  }
  const [intPart, fracPart = ''] = s.split('.');
  if (fracPart.length > scale) {
    // Refuse to silently truncate sub-atomic precision.
    throw new Error(`decimal ${s} has more than ${scale} fractional digits`);
  }
  return BigInt(intPart + (fracPart + '0'.repeat(scale)).slice(0, scale));
}

/** USD price -> USDC atomic units (USDC has 6 decimals). */
function usdToUsdcAtomic(usd) {
  return decimalToScaled(usd, 6).toString();
}

/**
 * USD price -> token atomic units given a USD-per-token rate. Rounds UP: an
 * off-by-one atomic unit in the payer's favour means we sold below the quoted
 * price, which compounds; one atomic unit the other way is dust.
 */
function usdToTokenAtomic(usd, usdPerToken, decimals) {
  const SCALE = 18;
  const usdScaled = decimalToScaled(usd, SCALE);
  const rateScaled = decimalToScaled(usdPerToken, SCALE);
  if (rateScaled === 0n) throw new Error('usdPerToken must be > 0');
  const numerator = usdScaled * 10n ** BigInt(decimals);
  return ((numerator + rateScaled - 1n) / rateScaled).toString();
}

module.exports = {
  NETWORKS,
  V1_NETWORK_SLUGS,
  USDC_DEFAULTS,
  load,
  decimalToScaled,
  usdToUsdcAtomic,
  usdToTokenAtomic,
};
