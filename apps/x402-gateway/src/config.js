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

/**
 * EVM network + asset allowlist for the SELF-HOSTED facilitator. A network
 * or token not in this table cannot be configured at all — fail closed.
 *
 * Every value below was verified LIVE (2026-08-15) with eth_call against
 * mainnet.base.org / sepolia.base.org: eth_chainId, name(), version(),
 * decimals(), DOMAIN_SEPARATOR(). CRITICAL live finding: Base MAINNET
 * USDC's EIP-712 domain name is "USD Coin" (the spec's examples show "USDC"
 * because they use Sepolia — advertising "USDC" on mainnet would make every
 * compliant client compute a wrong domain separator). The facilitator also
 * re-reads DOMAIN_SEPARATOR() from the live contract at startup/readyz and
 * refuses to serve on mismatch.
 */
const EVM_NETWORKS = {
  base: {
    slug: 'base',
    caip2: NETWORKS.BASE_MAINNET,
    chainId: 8453,
    usdc: {
      address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
      name: 'USD Coin',
      version: '2',
      decimals: 6,
      // live DOMAIN_SEPARATOR() 2026-08-15
      domainSeparator: '0x02fa7265e7c5d81118673727957699e4d68f74cd74b7db77da710fe8a2c7834f',
    },
    explorerTx: 'https://basescan.org/tx/',
  },
  'base-sepolia': {
    slug: 'base-sepolia',
    caip2: NETWORKS.BASE_SEPOLIA,
    chainId: 84532,
    usdc: {
      address: '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
      name: 'USDC',
      version: '2',
      decimals: 6,
      domainSeparator: '0x71f17a3b2ff373b803d70a5a07c046c1a2bc8e89c09ef722fcb047abe94c9818',
    },
    explorerTx: 'https://sepolia.basescan.org/tx/',
  },
};

function env(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

function envFrom(source, name, fallback) {
  const v = source[name];
  return v === undefined || v === '' ? fallback : v;
}

/** Strict non-negative BigInt env parse; garbage fails closed at startup. */
function parseBigIntEnv(source, name, fallback) {
  const v = envFrom(source, name, fallback);
  if (typeof v === 'bigint') return v;
  if (typeof v !== 'string' || !/^\d+$/.test(v)) {
    throw new Error(`${name} must be a non-negative decimal integer string, got ${JSON.stringify(v)}`);
  }
  return BigInt(v);
}

function parseIntEnv(source, name, fallback, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const v = envFrom(source, name, fallback);
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new Error(`${name} must be an integer in [${min}, ${max}], got ${JSON.stringify(v)}`);
  }
  return n;
}

function load(overrides = {}) {
  const networkEvm = env('X402_NETWORK_EVM', NETWORKS.BASE_SEPOLIA);
  const networkSvm = env('X402_NETWORK_SVM', NETWORKS.SOLANA_DEVNET);

  // Facilitator selection: self = our own facilitator-evm server on
  // loopback; remote = an external CDP-compatible facilitator URL. The
  // gateway/product layer is identical either way (x402 v2 §7 client).
  // Unset defaults to remote — that is what the live unit runs today.
  const facilitatorMode = env('X402_FACILITATOR_MODE', 'remote');
  if (facilitatorMode !== 'self' && facilitatorMode !== 'remote') {
    throw new Error(`X402_FACILITATOR_MODE must be "self" or "remote", got ${JSON.stringify(facilitatorMode)}`);
  }
  const evmFacilitatorPort = parseIntEnv(process.env, 'X402_EVM_FACILITATOR_PORT', 8743, { min: 1, max: 65535 });

  const cfg = {
    enabled: env('ANM_X402_ENABLED', '0') === '1',

    // Where the demo/gated resources say they live (v2 resource.url base).
    resourceBaseUrl: env('X402_RESOURCE_BASE_URL', 'http://127.0.0.1:4656'),
    serviceName: env('X402_SERVICE_NAME', 'Animica'),

    // Lane A: USDC on Base. mode=self talks to our own facilitator-evm
    // server (loopback, default :8743); mode=remote talks to an external
    // CDP-compatible facilitator (X402_FACILITATOR_URL, with the historic
    // X402_EVM_FACILITATOR_URL name honored for the live unit's env file).
    facilitatorMode,
    evmFacilitatorPort,
    networkEvm,
    usdcAsset: env('X402_USDC_ASSET', USDC_DEFAULTS[networkEvm] || ''),
    basePayTo: env('X402_BASE_PAYTO', ''), // EVM address that receives USDC
    evmFacilitatorUrl: facilitatorMode === 'self'
      ? `http://127.0.0.1:${evmFacilitatorPort}`
      : env('X402_FACILITATOR_URL', env('X402_EVM_FACILITATOR_URL', 'https://x402.org/facilitator')),
    // mainnet remote alternative (operator decision, see README):
    //   https://facilitator.payai.network               (PayAI)
    // Production goal is mode=self (our own facilitator-evm server).

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

/**
 * Strict, fail-closed configuration for the SELF-HOSTED EVM facilitator
 * (src/facilitator-evm/). Reads the spec's env model:
 *
 *   X402_NETWORK=base|base-sepolia   (allowlist — nothing else configures)
 *   X402_CHAIN_ID                    (optional; MUST match the network if set)
 *   X402_ASSET                       (optional; "USDC" or the exact allowlisted
 *                                     token address for the network)
 *   X402_RPC_URL                     (required, http(s))
 *   X402_RPC_FALLBACK_URL            (optional second RPC)
 *   X402_SETTLEMENT_ADDRESS          (required; the payTo that receives USDC —
 *                                     server-config only, never client-supplied)
 *   X402_FACILITATOR_PRIVATE_KEY     (required; validated by key.js loadSigner,
 *                                     only the derived address is ever logged)
 *   X402_EVM_FACILITATOR_PORT=8743   X402_FACILITATOR_BIND=127.0.0.1
 *   X402_MAX_GAS_PER_SETTLEMENT=150000
 *   X402_MAX_FEE_PER_GAS_WEI=1000000000        (1 gwei ~ 100x Base headroom)
 *   X402_DAILY_GAS_BUDGET_WEI=0                (0 = breaker disabled)
 *   X402_MIN_GAS_BALANCE_WEI=2000000000000000  (readyz floor, ~0.002 ETH)
 *   X402_CONFIRMATIONS=2
 *   X402_DB_PATH=./state/x402.db
 *   X402_RPC_TIMEOUT_MS=10000  X402_RPC_RETRIES=2
 *   X402_RECEIPT_TIMEOUT_MS=30000  X402_RECEIPT_POLL_MS=1000
 *   X402_EXPIRY_MARGIN_SECONDS=6
 *
 * Every contradiction (chain id vs network, asset vs allowlist) throws with
 * a precise message; the facilitator refuses to start.
 */
function loadEvmFacilitatorConfig(source = process.env) {
  const problems = [];
  const slug = envFrom(source, 'X402_NETWORK', 'base');
  const net = EVM_NETWORKS[slug];
  if (!net) {
    throw new Error(
      `X402_NETWORK=${JSON.stringify(slug)} is not allowlisted (allowed: ${Object.keys(EVM_NETWORKS).join(', ')})`
    );
  }

  const chainIdRaw = envFrom(source, 'X402_CHAIN_ID', String(net.chainId));
  if (!/^\d+$/.test(String(chainIdRaw)) || Number(chainIdRaw) !== net.chainId) {
    problems.push(`X402_CHAIN_ID=${chainIdRaw} contradicts X402_NETWORK=${slug} (expected ${net.chainId})`);
  }

  const assetRaw = envFrom(source, 'X402_ASSET', 'USDC');
  let asset = net.usdc.address;
  if (assetRaw !== 'USDC' && String(assetRaw).toLowerCase() !== net.usdc.address.toLowerCase()) {
    problems.push(
      `X402_ASSET=${assetRaw} is not allowlisted for ${slug} (allowed: "USDC" or ${net.usdc.address})`
    );
  }

  const rpcUrl = envFrom(source, 'X402_RPC_URL', '');
  if (!/^https?:\/\/.+/.test(rpcUrl)) problems.push('X402_RPC_URL is required and must be http(s)');
  const rpcFallbackUrl = envFrom(source, 'X402_RPC_FALLBACK_URL', '');
  if (rpcFallbackUrl && !/^https?:\/\/.+/.test(rpcFallbackUrl)) problems.push('X402_RPC_FALLBACK_URL must be http(s)');

  const settlementAddress = envFrom(source, 'X402_SETTLEMENT_ADDRESS', '');
  let payTo = '';
  try {
    payTo = require('./facilitator-evm/evm').validateAddress(settlementAddress, 'X402_SETTLEMENT_ADDRESS');
  } catch (e) {
    problems.push(e.message);
  }

  let cfg;
  try {
    cfg = {
      network: slug,
      caip2: net.caip2,
      chainId: net.chainId,
      asset,
      assetDecimals: net.usdc.decimals,
      eip712: { name: net.usdc.name, version: net.usdc.version },
      expectedDomainSeparator: net.usdc.domainSeparator,
      explorerTx: net.explorerTx,
      rpcUrl,
      rpcFallbackUrl: rpcFallbackUrl || null,
      settlementAddress: payTo,
      privateKey: envFrom(source, 'X402_FACILITATOR_PRIVATE_KEY', ''),
      bind: envFrom(source, 'X402_FACILITATOR_BIND', '127.0.0.1'),
      port: parseIntEnv(source, 'X402_EVM_FACILITATOR_PORT', 8743, { min: 1, max: 65535 }),
      maxGasPerSettlement: parseBigIntEnv(source, 'X402_MAX_GAS_PER_SETTLEMENT', 150000n),
      maxFeePerGasWei: parseBigIntEnv(source, 'X402_MAX_FEE_PER_GAS_WEI', 1000000000n),
      dailyGasBudgetWei: parseBigIntEnv(source, 'X402_DAILY_GAS_BUDGET_WEI', 0n),
      minGasBalanceWei: parseBigIntEnv(source, 'X402_MIN_GAS_BALANCE_WEI', 2000000000000000n),
      confirmations: parseIntEnv(source, 'X402_CONFIRMATIONS', 2, { min: 0, max: 500 }),
      dbPath: envFrom(source, 'X402_DB_PATH', './state/x402.db'),
      rpcTimeoutMs: parseIntEnv(source, 'X402_RPC_TIMEOUT_MS', 10000, { min: 100, max: 120000 }),
      rpcRetries: parseIntEnv(source, 'X402_RPC_RETRIES', 2, { min: 0, max: 10 }),
      receiptTimeoutMs: parseIntEnv(source, 'X402_RECEIPT_TIMEOUT_MS', 30000, { min: 1000, max: 600000 }),
      receiptPollMs: parseIntEnv(source, 'X402_RECEIPT_POLL_MS', 1000, { min: 50, max: 30000 }),
      expiryMarginSeconds: parseIntEnv(source, 'X402_EXPIRY_MARGIN_SECONDS', 6, { min: 0, max: 3600 }),
    };
  } catch (e) {
    problems.push(e.message);
  }

  if (!envFrom(source, 'X402_FACILITATOR_PRIVATE_KEY', '')) {
    problems.push('X402_FACILITATOR_PRIVATE_KEY is required (0600 env file; never committed)');
  }
  if (problems.length) {
    throw new Error('facilitator-evm config invalid (fail closed):\n  - ' + problems.join('\n  - '));
  }
  if (cfg.maxGasPerSettlement < 60000n) {
    // transferWithAuthorization real-world gasUsed is ~86k; a cap below the
    // floor means every settlement fails after claiming — refuse the footgun.
    throw new Error('X402_MAX_GAS_PER_SETTLEMENT below 60000 can never settle a transferWithAuthorization');
  }
  return cfg;
}

/**
 * Gateway/product configuration (src/server.js — the production entry that
 * supersedes demo-server.js). Reuses load() for the payment-lane keys (the
 * live unit's env names keep working unchanged) and adds the product
 * registry's knobs. Money knobs are validated fail-closed at load time:
 * a garbage price refuses startup, it does not fail at request time.
 *
 * `source` defaults to process.env; `overrides` win over everything (tests).
 */
function loadGatewayConfig(source = process.env, overrides = {}) {
  const base = load(overrides);
  const problems = [];

  const priceOf = (name, fallback) => {
    const v = envFrom(source, name, fallback);
    try {
      usdToUsdcAtomic(String(v)); // throws on non-decimal / negative / exponent
      return String(v);
    } catch (e) {
      problems.push(`${name}: ${e.message}`);
      return fallback;
    }
  };

  const envName = envFrom(source, 'X402_ENV', 'development');
  const echoEnabled = envName !== 'production' || envFrom(source, 'X402_ENABLE_ECHO', '') === '1';

  const walletsRaw = envFrom(source, 'X402_INFERENCE_WORKER_WALLETS', '');
  const inferenceWorkerWallets = walletsRaw
    ? walletsRaw.split(',').map((w) => w.trim()).filter(Boolean)
    : [];
  for (const w of inferenceWorkerWallets) {
    if (!/^anim1[a-z0-9]{10,100}$/.test(w)) {
      problems.push(`X402_INFERENCE_WORKER_WALLETS entry ${JSON.stringify(w)} is not a bech32m anim1 address`);
    }
  }

  let cfg;
  try {
    cfg = Object.assign({}, base, {
      env: envName,
      gatewayBind: envFrom(source, 'X402_GATEWAY_BIND', '127.0.0.1'),
      gatewayPort: parseIntEnv(source, 'X402_GATEWAY_PORT', 8742, { min: 1, max: 65535 }),
      gatewayDbPath: envFrom(source, 'X402_GATEWAY_DB_PATH', './state/x402-gateway.db'),

      // Animica node JSON-RPC (loopback ONLY in production — never the
      // public vhosts; see the chain-data recon).
      animicaRpcUrl: envFrom(source, 'X402_ANIMICA_RPC_URL', 'http://127.0.0.1:8545/rpc'),

      // Signed error receipts (payment-then-service failure policy). Empty
      // => an ephemeral per-boot key with a loud warning; production sets it.
      receiptHmacKey: envFrom(source, 'X402_RECEIPT_HMAC_KEY', ''),

      // Idempotency-Key replay store.
      idempotencyMaxBodyBytes: parseIntEnv(source, 'X402_IDEMPOTENCY_MAX_BODY_BYTES', 4_000_000, { min: 1024 }),
      idempotencyTtlSeconds: parseIntEnv(source, 'X402_IDEMPOTENCY_TTL_SECONDS', 7 * 24 * 3600, { min: 60 }),

      // P0 echo — development/settlement-smoke only.
      echoEnabled,

      // P1 QRNG.
      qrngEnabled: envFrom(source, 'X402_QRNG_ENABLED', '1') === '1',
      qrngPriceUsd: priceOf('X402_QRNG_PRICE_USDC', '0.01'),
      qrngMaxBytes: parseIntEnv(source, 'X402_QRNG_MAX_BYTES', 1024, { min: 1, max: 1048576 }),
      qrngTimeoutMs: parseIntEnv(source, 'X402_QRNG_TIMEOUT_MS', 5000, { min: 100, max: 60000 }),

      // P2 bulk chain data.
      bulkChainEnabled: envFrom(source, 'X402_BULK_CHAIN_ENABLED', '1') === '1',
      bulkChainPriceUsd: priceOf('X402_BULK_CHAIN_PRICE_USDC', '0.05'),
      bulkMaxBlocks: parseIntEnv(source, 'X402_BULK_MAX_BLOCKS', 1000, { min: 1, max: 10000 }),
      bulkMaxTxRecords: parseIntEnv(source, 'X402_BULK_MAX_TX_RECORDS', 10000, { min: 1, max: 1000000 }),
      bulkMaxResponseBytes: parseIntEnv(source, 'X402_BULK_MAX_RESPONSE_BYTES', 16_000_000, { min: 10_000 }),
      bulkExecTimeoutMs: parseIntEnv(source, 'X402_BULK_EXEC_TIMEOUT_MS', 25000, { min: 1000, max: 120000 }),
      bulkChunkBlocks: parseIntEnv(source, 'X402_BULK_CHUNK_BLOCKS', 100, { min: 1, max: 500 }),
      bulkHeadMargin: parseIntEnv(source, 'X402_BULK_HEAD_MARGIN', 6, { min: 0, max: 1000 }),

      // P3 priority inference — flagship LATER; hard-disabled by default and
      // additionally gated on live serving capacity (see src/capacity.js).
      priorityInferenceEnabled: envFrom(source, 'PRIORITY_INFERENCE_ENABLED', '0') === '1',
      priorityInferenceMinServingWorkers: parseIntEnv(source, 'PRIORITY_INFERENCE_MIN_SERVING_WORKERS', 2, { min: 1, max: 1000 }),
      inferencePriceUsd: priceOf('X402_INFERENCE_PRICE_USDC', '0.10'),
      inferenceWorkerWallets,
      inferenceTier: envFrom(source, 'X402_INFERENCE_TIER', 'standard'),
      inferenceUpstreamUrl: envFrom(source, 'X402_INFERENCE_UPSTREAM_URL', 'http://127.0.0.1:4600/v1/chat/completions'),
      inferenceTimeoutMs: parseIntEnv(source, 'X402_INFERENCE_TIMEOUT_MS', 180000, { min: 1000, max: 600000 }),
      inferenceMaxBodyBytes: parseIntEnv(source, 'X402_INFERENCE_MAX_BODY_BYTES', 262144, { min: 1024 }),
      capacityProbeIntervalMs: parseIntEnv(source, 'X402_CAPACITY_PROBE_INTERVAL_MS', 15000, { min: 1000, max: 300000 }),
      capacityMaxProbeAgeMs: parseIntEnv(source, 'X402_CAPACITY_MAX_PROBE_AGE_MS', 60000, { min: 1000, max: 3600000 }),
    });
  } catch (e) {
    problems.push(e.message);
  }
  if (problems.length) {
    throw new Error('gateway config invalid (fail closed):\n  - ' + problems.join('\n  - '));
  }
  return Object.assign(cfg, overrides);
}

module.exports = {
  NETWORKS,
  V1_NETWORK_SLUGS,
  USDC_DEFAULTS,
  EVM_NETWORKS,
  load,
  loadGatewayConfig,
  loadEvmFacilitatorConfig,
  decimalToScaled,
  usdToUsdcAtomic,
  usdToTokenAtomic,
};
