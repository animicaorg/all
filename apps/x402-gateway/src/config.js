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

/**
 * Strict decimal-string -> atomic BigInt env parse ("5.00" @6 -> 5000000n).
 * Never parseFloat: 2,658,950,900,358,165 does not survive Number, and a
 * money knob that silently rounds is a money knob that silently steals.
 */
function parseDecimalEnv(source, name, fallback, scale) {
  const v = envFrom(source, name, fallback);
  try {
    return decimalToScaled(String(v), scale);
  } catch (e) {
    throw new Error(`${name}: ${e.message}`);
  }
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
  // Base MAINNET is the shipped default: it is what the docs, the README and
  // the self-hosted facilitator's own default (X402_NETWORK=base) describe.
  // A testnet default here would silently contradict all three and quote
  // Sepolia USDC in production offers.
  const networkEvm = env('X402_NETWORK_EVM', NETWORKS.BASE_MAINNET);
  const networkSvm = env('X402_NETWORK_SVM', NETWORKS.SOLANA_DEVNET);

  // Facilitator selection: self = our own facilitator-evm server on loopback
  // (the DEFAULT — this stack is self-hosted and depends on no third-party
  // settlement service); remote = an external x402 v2 §7-compatible
  // facilitator URL, which must then be named explicitly. The
  // gateway/product layer is identical either way.
  const facilitatorMode = env('X402_FACILITATOR_MODE', 'self');
  if (facilitatorMode !== 'self' && facilitatorMode !== 'remote') {
    throw new Error(`X402_FACILITATOR_MODE must be "self" or "remote", got ${JSON.stringify(facilitatorMode)}`);
  }
  const evmFacilitatorPort = parseIntEnv(process.env, 'X402_EVM_FACILITATOR_PORT', 8743, { min: 1, max: 65535 });
  // mode=remote has NO default URL on purpose. A fallback here would send
  // real /verify and /settle traffic — other people's money — to whichever
  // third party the default names. Remote is an explicit operator decision.
  const remoteFacilitatorUrl = env('X402_FACILITATOR_URL', env('X402_EVM_FACILITATOR_URL', ''));
  if (facilitatorMode === 'remote' && !/^https?:\/\/.+/.test(remoteFacilitatorUrl)) {
    throw new Error(
      'X402_FACILITATOR_MODE=remote requires X402_FACILITATOR_URL (an http(s) x402 v2 §7 facilitator, e.g. https://facilitator.payai.network). '
      + 'There is deliberately no default: settlement must never fall back to an unnamed third party. Use X402_FACILITATOR_MODE=self for the built-in facilitator.'
    );
  }

  // A CDP facilitator URL without credentials is the worst of both worlds: the
  // gateway boots, health checks pass, every 402 is quoted normally, and then
  // EVERY settlement fails 401. Refuse at boot instead — the same class of
  // silent-break as the payTo mismatch that took settlement down in August.
  if (facilitatorMode === 'remote' && /(^|\.)cdp\.coinbase\.com/i.test(remoteFacilitatorUrl)) {
    const id = env('X402_CDP_API_KEY_ID', '');
    const secret = env('X402_CDP_API_KEY_SECRET', '');
    if (!id || !secret) {
      throw new Error(
        'X402_FACILITATOR_URL points at the CDP facilitator, which authenticates every /verify and '
        + '/settle with a JWT signed by a CDP API key. Set X402_CDP_API_KEY_ID and '
        + 'X402_CDP_API_KEY_SECRET, or this gateway would quote payments it can never settle.'
      );
    }
  }

  const cfg = {
    enabled: env('ANM_X402_ENABLED', '0') === '1',

    // Where the demo/gated resources say they live (v2 resource.url base).
    resourceBaseUrl: env('X402_RESOURCE_BASE_URL', 'http://127.0.0.1:4656'),
    serviceName: env('X402_SERVICE_NAME', 'Animica'),

    // Lane A: USDC on Base. mode=self talks to our own facilitator-evm
    // server (loopback, default :8743) — this is the default and the
    // production configuration; mode=remote talks to an external x402 v2
    // §7-compatible facilitator that the operator names explicitly in
    // X402_FACILITATOR_URL (the historic X402_EVM_FACILITATOR_URL name is
    // still honored for the live unit's env file). One documented remote
    // option is PayAI (https://facilitator.payai.network); there is no
    // default remote and no implicit third party.
    facilitatorMode,

    // ---------------------------------------------------------------------
    // CDP FACILITATOR CREDENTIALS (mode=remote against CDP).
    //
    // Operator-authorised 2026-08-19: route all endpoints through the CDP
    // facilitator so the catalog gets indexed into the Bazaar. Indexing is a
    // side effect of THAT facilitator settling a payment for an endpoint that
    // advertises Bazaar metadata, so settling elsewhere and being listed there
    // are mutually exclusive. This reverses the original self-hosted-only rule
    // deliberately; see src/facilitator-cdp/auth.js.
    //
    // Unset by default. mode stays `self` unless explicitly changed, so a
    // missing credential can never silently route money to a third party.
    cdpApiKeyId: env('X402_CDP_API_KEY_ID', ''),
    cdpApiKeySecret: env('X402_CDP_API_KEY_SECRET', ''),
    // Bazaar metadata in the 402 challenge. This is what makes an endpoint
    // indexable at all — without it the facilitator settles the payment and
    // indexes nothing.
    cdpBazaarDiscoverable: env('X402_CDP_BAZAAR_DISCOVERABLE', '1') === '1',

    evmFacilitatorPort,
    networkEvm,
    usdcAsset: env('X402_USDC_ASSET', USDC_DEFAULTS[networkEvm] || ''),
    // The asset's EIP-712 domain, advertised in the accepts entry as `extra`.
    //
    // Our own facilitator knows this from its network table, so it never needed
    // to be on the wire. A REMOTE facilitator does not: CDP rejects a
    // verification with "missing EIP-712 domain name/version in
    // requirements.extra" because it cannot rebuild the signing digest without
    // it. It is the same constant for every product, so adding it does not
    // change payment binding between routes.
    usdcDomainName: env('X402_USDC_DOMAIN_NAME',
      (Object.values(EVM_NETWORKS).find((n) => n.caip2 === networkEvm) || {}).usdc?.name || 'USD Coin'),
    usdcDomainVersion: env('X402_USDC_DOMAIN_VERSION',
      (Object.values(EVM_NETWORKS).find((n) => n.caip2 === networkEvm) || {}).usdc?.version || '2'),
    usdcDecimals: parseInt(env('X402_USDC_DECIMALS',
      String((Object.values(EVM_NETWORKS).find((n) => n.caip2 === networkEvm) || {}).usdc?.decimals || 6)), 10),
    basePayTo: env('X402_BASE_PAYTO', ''), // EVM address that receives USDC
    evmFacilitatorUrl: facilitatorMode === 'self'
      ? `http://127.0.0.1:${evmFacilitatorPort}`
      : remoteFacilitatorUrl,

    // Lane B: wANM (SPL token) via the LOCAL self-facilitator.
    networkSvm,
    wanmMint: env('WANM_MINT', ''), // real mint: solana.animica.org bridge config
    wanmTreasury: env('WANM_TREASURY', ''), // SPL owner wallet receiving wANM
    wanmDecimals: parseInt(env('WANM_DECIMALS', '9'), 10),
    wanmUsdPrice: env('WANM_USD_PRICE', ''), // USD per 1 wANM, decimal string
    wanmFeePayerPubkey: env('WANM_FEEPAYER_PUBKEY', ''), // sponsor pubkey advertised in extra.feePayer
    wanmFeePayerSecret: env('WANM_FEEPAYER_SECRET', ''), // hex/base58 32-byte ed25519 seed (facilitator only)
    // The wANM/SVM lane is RETIRED (bridge abandoned 2026-08-15) and its
    // facilitator keeps replay marks in memory only, so a restart would forget
    // them. Even fully configured it is not offered unless this is set — the
    // escape hatch exists for protocol-level tests and any future port to the
    // persistent store, not for production use.
    allowRetiredWanmLane: env('X402_ALLOW_RETIRED_WANM_LANE', '') === '1',
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
 * Treasury ("sweep and sip") configuration — see src/treasury/ and
 * docs/x402.md. Defaults are the spec's; every knob is validated fail-closed
 * here so a typo'd cold address or an impossible budget refuses startup
 * instead of misfiring at 3am with real money.
 *
 * The cold address must be EIP-55 checksummed EXACTLY. An all-lowercase
 * address is a valid EVM address and this loader would happily accept it —
 * but this one value is where every dollar of revenue ends up, and the
 * checksum is the only cheap way to catch a transposed character in it.
 *
 * A checksum is NOT enough on its own, though: the addresses most likely to
 * arrive by accident are all-digit and therefore have no case to get wrong.
 * `0x0000…0000` (an unset or templated variable, a truncated paste) and the
 * precompiles `0x…01`-`0x…09` all pass a checksum comparison unchanged, and a
 * burn address does not even revert — it would silently destroy every swept
 * dollar. Those are rejected outright below; a cold address that has CONTRACT
 * CODE is caught at runtime with one eth_getCode (see treasury.js
 * verifyColdAddress and X402_TREASURY_COLD_ALLOW_CONTRACT).
 */

/** Anything below this is the zero address or a precompile, never a wallet. */
const RESERVED_ADDRESS_CEILING = 0x10000n;

/** Well-known burn addresses: valid, checksummable, and unrecoverable. */
const BURN_ADDRESSES = new Set([
  '0x000000000000000000000000000000000000dEaD',
  '0x00000000000000000000000000000000DeaDBeef',
]);

function loadTreasuryConfig(source, { network, problems }) {
  const enabled = envFrom(source, 'X402_TREASURY_ENABLED', '0') === '1';
  const coldRaw = envFrom(source, 'X402_TREASURY_COLD_ADDRESS', '');
  let coldAddress = null;
  if (coldRaw) {
    try {
      const evmMod = require('./facilitator-evm/evm');
      const checksummed = evmMod.validateAddress(coldRaw, 'X402_TREASURY_COLD_ADDRESS');
      if (coldRaw !== checksummed) {
        problems.push(
          `X402_TREASURY_COLD_ADDRESS must be EIP-55 checksummed exactly (got ${coldRaw}, expected ${checksummed}) — `
          + 'this is the address every swept dollar goes to; the checksum is the typo guard'
        );
      } else if (BigInt(checksummed) < RESERVED_ADDRESS_CEILING) {
        problems.push(
          `X402_TREASURY_COLD_ADDRESS=${checksummed} is in the reserved low-address range (the zero address, the `
          + 'precompiles 0x…01-0x…09, 0x…dEaD). Those have no checksum to fail, so this is exactly the '
          + 'paste/template accident an EIP-55 comparison cannot catch. Real USDC reverts on a transfer to 0x0, '
          + 'which would disable sweeping and leave every dollar of revenue on the hot key.'
        );
      } else if (BURN_ADDRESSES.has(checksummed)) {
        problems.push(
          `X402_TREASURY_COLD_ADDRESS=${checksummed} is a burn address: a sweep to it succeeds and the money is gone`
        );
      } else {
        coldAddress = checksummed;
      }
    } catch (e) {
      problems.push(e.message);
    }
  } else if (enabled) {
    problems.push('X402_TREASURY_ENABLED=1 requires X402_TREASURY_COLD_ADDRESS (the sweep destination; server config only)');
  }

  let t;
  try {
    t = {
      enabled,
      coldAddress,
      // Sip trigger. 0.0005 ETH is ~900 settlements of remaining runway at
      // Base's typical 0.006 gwei — a trigger point, not a cliff, and
      // deliberately WELL ABOVE X402_MIN_GAS_BALANCE_WEI (the readiness
      // floor, 0.0001 ETH). The spec's original 1e14 default equalled the
      // readiness floor, so every refuel cycle could only ever begin at a
      // balance where /readyz was already 503: the facilitator advertised
      // itself unhealthy for the entire refuel. The two are now
      // cross-validated (floor >= 3x the readiness floor) below.
      ethFloorWei: parseBigIntEnv(source, 'X402_TREASURY_ETH_FLOOR_WEI', 500000000000000n),
      sipUsdcAtomic: parseDecimalEnv(source, 'X402_TREASURY_SIP_USDC', '5.00', 6),
      // The adaptive floor: never swap less than this, but DO swap less than
      // X402_TREASURY_SIP_USDC when that is all the revenue there is. A fixed
      // $5 minimum deadlocks after a gas spike (ETH gone at ~75 settlements
      // with ~$0.75 accrued); $0.50 already buys ~490 settlements of gas.
      sipMinUsdcAtomic: parseDecimalEnv(source, 'X402_TREASURY_SIP_MIN_USDC', '0.50', 6),
      maxSlippageBps: parseIntEnv(source, 'X402_TREASURY_MAX_SLIPPAGE_BPS', 100, { min: 1, max: 1000 }),
      sipCooldownS: parseIntEnv(source, 'X402_TREASURY_SIP_COOLDOWN_S', 86400, { min: 0, max: 30 * 86400 }),
      // After a FAILED attempt the cooldown is this instead. A stale-quote
      // revert is caught by the pre-flight estimate and costs nothing, so
      // waiting a full day to retry would be the expensive choice; the
      // two-strike breaker still bounds genuine failures.
      retryCooldownS: parseIntEnv(source, 'X402_TREASURY_RETRY_COOLDOWN_S', 900, { min: 0, max: 86400 }),
      dailySwapBudgetAtomic: parseDecimalEnv(source, 'X402_TREASURY_DAILY_SWAP_BUDGET_USDC', '10.00', 6),
      usdcCeilingAtomic: parseDecimalEnv(source, 'X402_TREASURY_USDC_CEILING', '20.00', 6),
      checkIntervalS: parseIntEnv(source, 'X402_TREASURY_CHECK_INTERVAL_S', 300, { min: 10, max: 86400 }),
      // Dust guard: without it a balance hovering one atomic unit above the
      // ceiling would pay ~63k gas to sweep $0.000001 every interval.
      minSweepAtomic: parseDecimalEnv(source, 'X402_TREASURY_MIN_SWEEP_USDC', '0.10', 6),
      // Fee tiers to quote, best fill wins. 500 is the deepest USDC/WETH pool
      // on Base; 100 is marginally cheaper but ~26x thinner. 10000 is not
      // allowlisted at all — its 1% fee eats twenty times the sip's overhead.
      poolFees: String(envFrom(source, 'X402_TREASURY_POOL_FEES', '500,100')).split(',').map((s) => Number(s.trim())),
      swapDeadlineS: parseIntEnv(source, 'X402_TREASURY_SWAP_DEADLINE_S', 180, { min: 30, max: 3600 }),
      maxSwapGas: parseBigIntEnv(source, 'X402_TREASURY_MAX_SWAP_GAS', 300000n),
      maxApproveGas: parseBigIntEnv(source, 'X402_TREASURY_MAX_APPROVE_GAS', 100000n),
      maxSweepGas: parseBigIntEnv(source, 'X402_TREASURY_MAX_SWEEP_GAS', 150000n),
      // Refuse to convert revenue when the ETH bought is not worth several
      // times the gas the conversion costs. Priced on the MEASURED gas of the
      // legs this attempt will really send, at the fee it will really pay
      // (baseFee + tip) — see treasury.js priceForAmount. Pricing the CAPS at
      // the fee CEILING (the previous behaviour, ratio 20) demanded ~90x the
      // true cost and vetoed every sip above ~0.17 gwei, i.e. it disabled the
      // bootstrap-stall cure precisely in the gas spike that needs it.
      minEthOutGasRatio: parseIntEnv(source, 'X402_TREASURY_MIN_ETH_OUT_GAS_RATIO', 4, { min: 1, max: 100000 }),
      maxConsecutiveFailures: parseIntEnv(source, 'X402_TREASURY_MAX_CONSECUTIVE_FAILURES', 2, { min: 1, max: 10 }),
      // How far a quote may sit below an INDEPENDENT reference (the optional
      // X402_ETH_USD_PRICE, and our own last realised sip rate) before the sip
      // is skipped. The slippage bound alone is anchored to the pool it
      // protects: a manipulated or stale quote drags amountOutMinimum down
      // with it, and the fill then looks like a clean success.
      maxQuoteDeviationBps: parseIntEnv(source, 'X402_TREASURY_MAX_QUOTE_DEVIATION_BPS', 5000, { min: 100, max: 9000 }),
      // A realised rate older than this is not a price reference any more.
      rateReferenceMaxAgeS: parseIntEnv(source, 'X402_TREASURY_RATE_REFERENCE_MAX_AGE_S', 7 * 86400, { min: 3600, max: 90 * 86400 }),
      // Sweeps have no cooldown (draining fast is the point), so they get a
      // per-day count cap instead — otherwise their gas is bounded by nothing
      // the operator configured.
      maxSweepsPerDay: parseIntEnv(source, 'X402_TREASURY_MAX_SWEEPS_PER_DAY', 24, { min: 1, max: 1000 }),
      // A treasury transaction still pending after this long is bumped
      // (same nonce, higher fee): every settlement is queued behind it.
      stuckTxS: parseIntEnv(source, 'X402_TREASURY_STUCK_TX_S', 180, { min: 30, max: 3600 }),
      maxTxBumps: parseIntEnv(source, 'X402_TREASURY_MAX_TX_BUMPS', 3, { min: 0, max: 10 }),
      // Consecutive checks under the ETH floor with the sip SKIPPED before
      // /readyz raises the "cannot refuel" warning.
      refuelAlertTicks: parseIntEnv(source, 'X402_TREASURY_REFUEL_ALERT_TICKS', 3, { min: 1, max: 100 }),
      // Cross-process signing lease (service vs `treasury sip|sweep --confirm`).
      leaseTtlS: parseIntEnv(source, 'X402_TREASURY_LEASE_TTL_S', 900, { min: 60, max: 86400 }),
      // A cold address with contract code is refused unless declared (a safe
      // or multisig is legitimate — it just has to be deliberate).
      coldAllowContract: envFrom(source, 'X402_TREASURY_COLD_ALLOW_CONTRACT', '0') === '1',
    };
  } catch (e) {
    problems.push(e.message);
    return { enabled, coldAddress };
  }

  if (enabled) {
    // A treasury on a network with no live-verified router/quoter/pool set
    // would have to guess contract addresses for a swap that spends revenue.
    const { CONTRACTS } = require('./treasury/uniswap');
    if (!CONTRACTS[network]) {
      problems.push(
        `X402_TREASURY_ENABLED=1 is not supported on X402_NETWORK=${network}: no live-verified Uniswap v3 contract set `
        + `(supported: ${Object.keys(CONTRACTS).join(', ')})`
      );
    }
  }

  const ALLOWED_FEES = [100, 500, 3000];
  if (t.poolFees.length === 0 || t.poolFees.some((f) => !ALLOWED_FEES.includes(f))) {
    problems.push(`X402_TREASURY_POOL_FEES must be a comma list drawn from ${ALLOWED_FEES.join(',')} (got ${t.poolFees.join(',')})`);
  }
  if (t.sipMinUsdcAtomic <= 0n) {
    problems.push('X402_TREASURY_SIP_MIN_USDC must be > 0 (a zero-size sip can only revert)');
  }
  if (t.sipUsdcAtomic < t.sipMinUsdcAtomic) {
    problems.push('X402_TREASURY_SIP_USDC must be >= X402_TREASURY_SIP_MIN_USDC');
  }
  if (t.dailySwapBudgetAtomic < t.sipMinUsdcAtomic) {
    problems.push('X402_TREASURY_DAILY_SWAP_BUDGET_USDC is below X402_TREASURY_SIP_MIN_USDC, so no sip could ever run');
  }
  if (t.usdcCeilingAtomic < t.sipMinUsdcAtomic) {
    problems.push(
      'X402_TREASURY_USDC_CEILING is below X402_TREASURY_SIP_MIN_USDC: the sweep would drain the wallet past the point '
      + 'where it can buy its own gas back'
    );
  }
  if (t.ethFloorWei <= 0n) {
    problems.push('X402_TREASURY_ETH_FLOOR_WEI must be > 0 (a zero floor never triggers a sip)');
  }
  if (t.ethFloorWei > 10n ** 18n) {
    problems.push('X402_TREASURY_ETH_FLOOR_WEI above 1 ETH would sip continuously; the floor is a bootstrap trigger, not a target balance');
  }
  if (t.maxSwapGas < 200000n) {
    // The measured multicall is 165,389 gas; a cap below ~200k leaves no
    // headroom for a tick-crossing swap and turns every sip into a cap error.
    problems.push('X402_TREASURY_MAX_SWAP_GAS below 200000 cannot fit the measured 165k-gas swap+unwrap multicall');
  }
  if (t.leaseTtlS < 2 * t.checkIntervalS) {
    // The service renews the lease once per tick. A TTL shorter than two
    // ticks would let it lapse between renewals, and a CLI sip could then
    // take the nonce lane out from under a running facilitator.
    problems.push(
      `X402_TREASURY_LEASE_TTL_S (${t.leaseTtlS}) must be at least 2x X402_TREASURY_CHECK_INTERVAL_S (${t.checkIntervalS}): `
      + 'the running service renews the signing lease once per check interval'
    );
  }
  return t;
}

/**
 * Cross-check the treasury against the facilitator knobs it interacts with.
 * Kept separate because these need the fully built config, not just the
 * treasury block.
 */
function validateTreasuryAgainstFacilitator(cfg, problems) {
  const t = cfg.treasury;
  if (!t || !t.enabled) return;
  // The refuel trigger must sit meaningfully ABOVE the readiness floor.
  // Equal (the shipped default before this fix) means the treasury may only
  // ever act at a balance where /readyz is already 503: every refuel begins
  // with the facilitator advertising itself unhealthy, and any supervisor or
  // load balancer gating on /readyz flaps mid-sip. Worse, a readiness floor
  // ABOVE the sip trigger creates a dead band in which the facilitator
  // refuses traffic while the treasury reports "above_eth_floor" and does
  // nothing at all.
  if (t.ethFloorWei < 3n * cfg.minGasBalanceWei) {
    problems.push(
      `X402_TREASURY_ETH_FLOOR_WEI (${t.ethFloorWei}) must be at least 3x X402_MIN_GAS_BALANCE_WEI `
      + `(${cfg.minGasBalanceWei}): the treasury has to start refuelling well before /readyz fails on gas_balance, `
      + 'or the facilitator spends every refuel cycle reporting itself not-ready (and below the readiness floor it '
      + 'refuses the very traffic that pays for the gas)'
    );
  }
  // When the operator gave us a price, check that the SMALLEST allowed sip
  // can actually restore readiness. A sip that cannot clear the floor turns
  // the refuel loop into a slow leak.
  if (cfg.ethUsdPrice && cfg.ethUsdPrice > 0n) {
    const minSipWei = (t.sipMinUsdcAtomic * 1_000_000_000_000n) / cfg.ethUsdPrice;
    if (minSipWei < cfg.minGasBalanceWei) {
      problems.push(
        `X402_TREASURY_SIP_MIN_USDC buys ~${minSipWei} wei at X402_ETH_USD_PRICE=${cfg.ethUsdPrice}, which is below `
        + `X402_MIN_GAS_BALANCE_WEI (${cfg.minGasBalanceWei}): the smallest permitted sip could not restore readiness`
      );
    }
  }
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
      // Circuit breaker ON by default. 0.0004 ETH/day is ~600 settlements at
      // Base's typical 0.006 gwei — generous for real traffic, but it caps a
      // gas-drain attack at cents/day instead of draining the whole float.
      // Set to 0 to disable (not recommended).
      dailyGasBudgetWei: parseBigIntEnv(source, 'X402_DAILY_GAS_BUDGET_WEI', 400000000000000n),
      // Readiness floor: below this the facilitator reports not-ready and stops
      // accepting settlements rather than stranding a paid-but-unsettleable
      // request. 0.0001 ETH ~ 150 settlements of headroom at typical Base gas;
      // deliberately small so a bootstrap float (a few dollars) can run.
      minGasBalanceWei: parseBigIntEnv(source, 'X402_MIN_GAS_BALANCE_WEI', 100000000000000n),
      // Optional economic floor: when set (integer USD per ETH, e.g. 3000),
      // a settlement is refused when its sponsored gas would cost more than
      // the USDC it collects divided by this safety multiple. Unset = skip.
      ethUsdPrice: parseBigIntEnv(source, 'X402_ETH_USD_PRICE', 0n),
      gasSafetyMultiple: parseIntEnv(source, 'X402_GAS_SAFETY_MULTIPLE', 2, { min: 1, max: 100 }),
      confirmations: parseIntEnv(source, 'X402_CONFIRMATIONS', 2, { min: 0, max: 500 }),
      dbPath: envFrom(source, 'X402_DB_PATH', './state/x402.db'),
      rpcTimeoutMs: parseIntEnv(source, 'X402_RPC_TIMEOUT_MS', 10000, { min: 100, max: 120000 }),
      rpcRetries: parseIntEnv(source, 'X402_RPC_RETRIES', 2, { min: 0, max: 10 }),
      receiptTimeoutMs: parseIntEnv(source, 'X402_RECEIPT_TIMEOUT_MS', 30000, { min: 1000, max: 600000 }),
      receiptPollMs: parseIntEnv(source, 'X402_RECEIPT_POLL_MS', 1000, { min: 50, max: 30000 }),
      expiryMarginSeconds: parseIntEnv(source, 'X402_EXPIRY_MARGIN_SECONDS', 6, { min: 0, max: 3600 }),
      // "Sweep and sip" treasury (src/treasury/). Default OFF; single-wallet
      // mode (payTo == facilitator address) refuses to start without it, but
      // that check needs the derived address and so lives in
      // createEvmFacilitator via assertTreasuryPolicy.
      treasury: loadTreasuryConfig(source, { network: slug, problems }),
    };
  } catch (e) {
    problems.push(e.message);
  }

  if (!envFrom(source, 'X402_FACILITATOR_PRIVATE_KEY', '')) {
    problems.push('X402_FACILITATOR_PRIVATE_KEY is required (0600 env file; never committed)');
  }
  if (cfg && cfg.treasury) validateTreasuryAgainstFacilitator(cfg, problems);
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

  // PRICING FLOOR — read before lowering any default below.
  //
  // Every paid call settles a real EIP-3009 transfer on Base and WE sponsor that
  // gas. Measured on the first production settlement (2026-08-15, tx
  // 0x3433107e…): 515,712,375,115 wei actually spent, 1,192,268,000,000 wei
  // reserved. That is ~$0.0018 spent / ~$0.0042 reserved per settlement at
  // ETH $3,500 — so a $0.001 endpoint, which is the going rate for cheap x402
  // listings elsewhere, would lose money on every single call here.
  // checkEconomicFloor() (src/facilitator-evm/gas.js) refuses to settle when
  // reserved gas x2 exceeds the payment, which puts the hard floor near $0.0084
  // at that ETH price and above $0.01 if ETH reaches ~$4,500.
  //
  // Hence $0.01 is the FLOOR for anything that settles per call, not a chosen
  // price, and the way to go cheaper per unit is to amortise one settlement over
  // many units (see qrng/bulk: independent draws, one settlement) — not to
  // shade the per-call number down.
  //
  // The prices below sit well above that floor (2026-08-15). They were briefly
  // cut to the floor on the theory that price was why nothing sold; the traffic
  // said otherwise — every visitor was an indexer or an uptime monitor, none
  // ever presented a payment, and published x402 adoption data puts the average
  // paid call near $0.30. We were 10-30x UNDER market, not over it. Sitting on
  // the floor also made settlement fragile: at $0.01 the reserve-based
  // gas_exceeds_payment_value check clears by ~1.2x, so a rise in ETH toward
  // ~$4,500 would have started REFUSING settlements outright. The headroom here
  // is a correctness property, not greed.
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

  // Production fail-closed checks. Both of these are values a buyer is told
  // to rely on, so a silent development default in production is a broken
  // promise, not a warning:
  //   - X402_RESOURCE_BASE_URL is interpolated into every 402 resource.url
  //     AND into the commit-reveal `reveal_url` a buyer publishes to their
  //     players. Left at the loopback default it sells an audit link that
  //     resolves to the buyer's own machine.
  //   - X402_RECEIPT_HMAC_KEY signs the error receipts that entitle a refund.
  //     A per-boot ephemeral key makes them unverifiable after a restart.
  if (envName === 'production') {
    const rb = envFrom(source, 'X402_RESOURCE_BASE_URL', '');
    if (!/^https?:\/\/.+/.test(rb) || /^https?:\/\/(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])/i.test(rb)) {
      problems.push(
        'X402_RESOURCE_BASE_URL must be the public base URL (e.g. https://animica.dev) when X402_ENV=production — '
        + 'it is published in every 402 resource.url and in the commit-reveal reveal_url a buyer hands to their players'
      );
    }
    if (!envFrom(source, 'X402_RECEIPT_HMAC_KEY', '')) {
      problems.push('X402_RECEIPT_HMAC_KEY is required when X402_ENV=production (it signs the error receipts that entitle reconciliation/refund)');
    }
  }

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
      // The FACILITATOR's payments ledger (same name the facilitator config
      // reads). The gateway opens it READ-ONLY for GET /x402/stats and for
      // nothing else — settlements are written by the facilitator process.
      settlementDbPath: envFrom(source, 'X402_DB_PATH', './state/x402.db'),

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
      qrngPriceUsd: priceOf('X402_QRNG_PRICE_USDC', '0.05'),
      qrngMaxBytes: parseIntEnv(source, 'X402_QRNG_MAX_BYTES', 1024, { min: 1, max: 1048576 }),
      qrngTimeoutMs: parseIntEnv(source, 'X402_QRNG_TIMEOUT_MS', 5000, { min: 100, max: 60000 }),

      // P1b the randomness FAMILY (int/shuffle/pick/bulk/commit-reveal).
      // Every one of them derives from a single qrng draw, so they share the
      // qrng timeout/health gate; only the caps and prices live here.
      randomEnabled: envFrom(source, 'X402_RANDOM_ENABLED', '1') === '1',
      randomIntPriceUsd: priceOf('X402_RANDOM_INT_PRICE_USDC', '0.05'),
      randomShufflePriceUsd: priceOf('X402_RANDOM_SHUFFLE_PRICE_USDC', '0.05'),
      randomPickPriceUsd: priceOf('X402_RANDOM_PICK_PRICE_USDC', '0.05'),
      randomBulkPriceUsd: priceOf('X402_RANDOM_BULK_PRICE_USDC', '0.20'),
      randomCommitPriceUsd: priceOf('X402_RANDOM_COMMIT_PRICE_USDC', '0.10'),
      // Bytes drawn from the node for a DERIVED product. 32 is a full DRNG
      // seed: the SHA3 counter-mode stream expands it without bound, so a
      // bigger draw would buy nothing and only bloat the published bytes the
      // buyer has to re-hash. (random_bulk is different — there the bytes
      // ARE the product, so it draws draws*bytes.)
      randomSeedBytes: parseIntEnv(source, 'X402_RANDOM_SEED_BYTES', 32, { min: 16, max: 1024 }),
      randomMaxInts: parseIntEnv(source, 'X402_RANDOM_MAX_INTS', 1000, { min: 1, max: 100000 }),
      randomMaxItems: parseIntEnv(source, 'X402_RANDOM_MAX_ITEMS', 10000, { min: 1, max: 1000000 }),
      randomMaxPicks: parseIntEnv(source, 'X402_RANDOM_MAX_PICKS', 1000, { min: 1, max: 100000 }),
      randomMaxBodyBytes: parseIntEnv(source, 'X402_RANDOM_MAX_BODY_BYTES', 512_000, { min: 1024 }),
      // Output cap, checked BEFORE settlement. Input caps alone do not bound
      // the RESPONSE: `pick` with replace:true may emit the same large item k
      // times, so a 512 KB request could otherwise ask for a ~512 MB answer
      // (measured 1000x amplification, >2 GB RSS) for $0.02. The estimate is
      // computed from the parsed request, so an oversize answer is a 400 that
      // was never sold.
      randomMaxResponseBytes: parseIntEnv(source, 'X402_RANDOM_MAX_RESPONSE_BYTES', 4_000_000, { min: 4096 }),
      randomBulkMaxDraws: parseIntEnv(source, 'X402_RANDOM_BULK_MAX_DRAWS', 10, { min: 1, max: 1000 }),
      randomMaxDrawBytes: parseIntEnv(source, 'X402_RANDOM_MAX_DRAW_BYTES', 65536, { min: 32, max: 1048576 }),
      randomCommitMaxDelaySec: parseIntEnv(source, 'X402_RANDOM_COMMIT_MAX_DELAY_SECONDS', 7 * 24 * 3600, { min: 0, max: 365 * 24 * 3600 }),
      // Retention for sealed/revealed commitments. The free reveal route is
      // only as good as this window — say it in the 404 body, not in a FAQ.
      randomCommitTtlSeconds: parseIntEnv(source, 'X402_RANDOM_COMMIT_TTL_SECONDS', 90 * 24 * 3600, { min: 3600 }),

      // P2 bulk chain data.
      bulkChainEnabled: envFrom(source, 'X402_BULK_CHAIN_ENABLED', '1') === '1',
      bulkChainPriceUsd: priceOf('X402_BULK_CHAIN_PRICE_USDC', '0.25'),
      bulkMaxBlocks: parseIntEnv(source, 'X402_BULK_MAX_BLOCKS', 1000, { min: 1, max: 10000 }),
      bulkMaxTxRecords: parseIntEnv(source, 'X402_BULK_MAX_TX_RECORDS', 10000, { min: 1, max: 1000000 }),
      bulkMaxResponseBytes: parseIntEnv(source, 'X402_BULK_MAX_RESPONSE_BYTES', 16_000_000, { min: 10_000 }),
      bulkExecTimeoutMs: parseIntEnv(source, 'X402_BULK_EXEC_TIMEOUT_MS', 25000, { min: 1000, max: 120000 }),
      bulkChunkBlocks: parseIntEnv(source, 'X402_BULK_CHUNK_BLOCKS', 100, { min: 1, max: 500 }),
      bulkHeadMargin: parseIntEnv(source, 'X402_BULK_HEAD_MARGIN', 6, { min: 0, max: 1000 }),

      // P4 account history — served from the gateway's OWN sqlite address
      // index (src/chain-index/), because no account-history index exists
      // anywhere on this box: the node RPC has no history method and the
      // explorer scans at most 250 blocks / 3.5 s per call.
      chainIndexEnabled: envFrom(source, 'X402_CHAIN_INDEX_ENABLED', '1') === '1',
      chainIndexDbPath: envFrom(source, 'X402_CHAIN_INDEX_DB_PATH', './state/x402-chain-index.db'),
      // Walker politeness — the node serializes ALL RPC on one event loop
      // shared with miner getwork and wallets (measured: a 1000-block batch
      // holds it 5.8-8.6 s; 100 blocks ~0.43 s).
      chainIndexChunkBlocks: parseIntEnv(source, 'X402_CHAIN_INDEX_CHUNK_BLOCKS', 100, { min: 1, max: 500 }),
      chainIndexChunkPauseMs: parseIntEnv(source, 'X402_CHAIN_INDEX_CHUNK_PAUSE_MS', 50, { min: 0, max: 10000 }),
      chainIndexPollMs: parseIntEnv(source, 'X402_CHAIN_INDEX_POLL_MS', 15000, { min: 250, max: 600000 }),
      chainIndexBatchTimeoutMs: parseIntEnv(source, 'X402_CHAIN_INDEX_BATCH_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),
      // Never index a block a shallow reorg could still replace.
      chainIndexHeadMargin: parseIntEnv(source, 'X402_CHAIN_INDEX_HEAD_MARGIN', 6, { min: 0, max: 1000 }),
      chainIndexReorgRewind: parseIntEnv(source, 'X402_CHAIN_INDEX_REORG_REWIND', 64, { min: 1, max: 100000 }),
      // Freshness gate: above this lag (or this tick age) the history product
      // reports available:false and never asks for payment.
      chainIndexMaxLagBlocks: parseIntEnv(source, 'X402_CHAIN_INDEX_MAX_LAG_BLOCKS', 12, { min: 1, max: 100000 }),
      chainIndexMaxTickAgeMs: parseIntEnv(source, 'X402_CHAIN_INDEX_MAX_TICK_AGE_MS', 300000, { min: 1000, max: 86400000 }),

      chainHistoryEnabled: envFrom(source, 'X402_CHAIN_HISTORY_ENABLED', '1') === '1',
      chainHistoryPriceUsd: priceOf('X402_CHAIN_HISTORY_PRICE_USDC', '0.15'),
      chainHistoryMaxLimit: parseIntEnv(source, 'X402_CHAIN_HISTORY_MAX_LIMIT', 500, { min: 1, max: 10000 }),
      chainHistoryDefaultLimit: parseIntEnv(source, 'X402_CHAIN_HISTORY_DEFAULT_LIMIT', 100, { min: 1, max: 10000 }),

      // P5 bulk balances — measured ~5 ms/address batched, so 500/request.
      chainBalancesEnabled: envFrom(source, 'X402_CHAIN_BALANCES_ENABLED', '1') === '1',
      chainBalancesPriceUsd: priceOf('X402_CHAIN_BALANCES_PRICE_USDC', '0.10'),
      chainBalancesMaxAddresses: parseIntEnv(source, 'X402_CHAIN_BALANCES_MAX_ADDRESSES', 500, { min: 1, max: 5000 }),
      chainBalancesTimeoutMs: parseIntEnv(source, 'X402_CHAIN_BALANCES_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),

      // P3 priority inference — flagship LATER; hard-disabled by default and
      // additionally gated on live serving capacity (see src/capacity.js).
      priorityInferenceEnabled: envFrom(source, 'PRIORITY_INFERENCE_ENABLED', '0') === '1',
      priorityInferenceMinServingWorkers: parseIntEnv(source, 'PRIORITY_INFERENCE_MIN_SERVING_WORKERS', 2, { min: 1, max: 1000 }),
      inferencePriceUsd: priceOf('X402_INFERENCE_PRICE_USDC', '0.30'),
      inferenceWorkerWallets,
      inferenceTier: envFrom(source, 'X402_INFERENCE_TIER', 'standard'),
      inferenceUpstreamUrl: envFrom(source, 'X402_INFERENCE_UPSTREAM_URL', 'http://127.0.0.1:4600/v1/chat/completions'),
      // Optional bearer for the upstream. Unset => no auth header is sent.
      inferenceUpstreamKey: envFrom(source, 'X402_INFERENCE_UPSTREAM_KEY', ''),
      // Degraded-delivery circuit breaker. The capacity gate reads chain
      // metadata only (registered / last_seen / tiers) and cannot see that a
      // worker fails to load a model — so real delivery failures feed back
      // here and withhold the product instead of charging for an apology.
      inferenceBreakerTrips: parseIntEnv(source, 'X402_INFERENCE_BREAKER_TRIPS', 2, { min: 1, max: 100 }),
      inferenceBreakerCooldownMs: parseIntEnv(source, 'X402_INFERENCE_BREAKER_COOLDOWN_MS', 300000, { min: 1000 }),
      inferenceTimeoutMs: parseIntEnv(source, 'X402_INFERENCE_TIMEOUT_MS', 180000, { min: 1000, max: 600000 }),
      inferenceMaxBodyBytes: parseIntEnv(source, 'X402_INFERENCE_MAX_BODY_BYTES', 262144, { min: 1024 }),
      capacityProbeIntervalMs: parseIntEnv(source, 'X402_CAPACITY_PROBE_INTERVAL_MS', 15000, { min: 1000, max: 300000 }),
      capacityMaxProbeAgeMs: parseIntEnv(source, 'X402_CAPACITY_MAX_PROBE_AGE_MS', 60000, { min: 1000, max: 3600000 }),

      // Paid media rendering (image / video / audio) via the GPU-miner queue.
      // Priced per cost family, not per kind: a 5 s video is not an image.
      // Enabled by default — unlike priority inference, the media queue has
      // had renderers online continuously, and the per-kind gate below refuses
      // the sale on its own the moment that stops being true.
      mediaEnabled: envFrom(source, 'X402_MEDIA_ENABLED', '1') === '1',
      mediaImagePriceUsd: priceOf('X402_MEDIA_IMAGE_PRICE_USDC', '0.10'),
      mediaVideoPriceUsd: priceOf('X402_MEDIA_VIDEO_PRICE_USDC', '0.50'),
      mediaAudioPriceUsd: priceOf('X402_MEDIA_AUDIO_PRICE_USDC', '0.20'),
      // One online renderer for the requested kind is a real renderer; the
      // inference floor of 2 exists because that pool is one machine deep and
      // flaps. Raise this if paid renders start queueing behind free ones.
      mediaMinRenderers: parseIntEnv(source, 'X402_MEDIA_MIN_RENDERERS', 1, { min: 1, max: 100 }),
      mediaCapabilitiesUrl: envFrom(source, 'X402_MEDIA_CAPABILITIES_URL', 'https://animica.dev/api/mkt/v1/media/capabilities'),
      mediaSubmitUrl: envFrom(source, 'X402_MEDIA_SUBMIT_URL', 'https://animica.dev/api/mkt/v1/media/jobs'),
      // Used to absolutise the queue's relative poll_url for a remote agent.
      mediaPublicBase: envFrom(source, 'X402_MEDIA_PUBLIC_BASE', 'https://animica.dev'),
      mediaProbeTimeoutMs: parseIntEnv(source, 'X402_MEDIA_PROBE_TIMEOUT_MS', 8000, { min: 500, max: 60000 }),
      mediaSubmitTimeoutMs: parseIntEnv(source, 'X402_MEDIA_SUBMIT_TIMEOUT_MS', 30000, { min: 1000, max: 120000 }),
      mediaProbeIntervalMs: parseIntEnv(source, 'X402_MEDIA_PROBE_INTERVAL_MS', 20000, { min: 1000, max: 300000 }),
      // The queue accepts 12 MB (up to 6 images for i2v); mirror that so a
      // caller is not rejected here with a different limit than the free path.
      mediaMaxBodyBytes: parseIntEnv(source, 'X402_MEDIA_MAX_BODY_BYTES', 12 * 1024 * 1024, { min: 1024 }),
      mediaMaxProbeAgeMs: parseIntEnv(source, 'X402_MEDIA_MAX_PROBE_AGE_MS', 90000, { min: 1000, max: 3600000 }),

      // Free trials — let an agent evaluate before it pays. Caps are per client
      // per UTC day and are sized to what a product costs US to serve, not to
      // what it sells for: a node read is nearly free, a GPU render is not.
      // 0 disables the trial route for that family entirely.
      trialsEnabled: envFrom(source, 'X402_TRIALS_ENABLED', '1') === '1',
      // Deterministic node/index reads — cheap, and the most useful to sample
      // because the agent is really checking response shape and freshness.
      trialLimitCheap: parseIntEnv(source, 'X402_TRIAL_LIMIT_CHEAP', 5, { min: 0, max: 1000 }),
      // Entropy draws: cheap to serve but each one consumes a real attested
      // draw, so a smaller allowance.
      trialLimitRandom: parseIntEnv(source, 'X402_TRIAL_LIMIT_RANDOM', 3, { min: 0, max: 1000 }),
      // Inference: a community GPU spends real seconds on this.
      trialLimitInference: parseIntEnv(source, 'X402_TRIAL_LIMIT_INFERENCE', 1, { min: 0, max: 100 }),
      // Media: minutes of GPU time per call. One per day is a customer
      // acquisition cost, not a service tier.
      trialLimitMedia: parseIntEnv(source, 'X402_TRIAL_LIMIT_MEDIA', 1, { min: 0, max: 100 }),
      // Two, not one: an audit reaches a third-party origin, and a transient
      // failure there burns a trial. One free call would lock a prospect out
      // for 24 hours over a network blip on someone else's server.
      trialLimitSolve: parseIntEnv(source, 'X402_TRIAL_LIMIT_SOLVE', 2, { min: 0, max: 100 }),
      trialLimitMeshProbe: parseIntEnv(source, 'X402_TRIAL_LIMIT_MESH_PROBE', 3, { min: 0, max: 100 }),
      trialLimitMeshFind: parseIntEnv(source, 'X402_TRIAL_LIMIT_MESH_FIND', 3, { min: 0, max: 100 }),
      // Analytics trials are the cheapest way for a merchant to see their own
      // pricing position, which is the single most persuasive sample this
      // gateway can give away — but each one costs an AICF narration call.
      trialLimitAnalytics: parseIntEnv(source, 'X402_TRIAL_LIMIT_ANALYTICS', 2, { min: 0, max: 100 }),
      trialLimitGeoFix: parseIntEnv(source, 'X402_TRIAL_LIMIT_GEO_FIX', 1, { min: 0, max: 100 }),
      trialLimitGeoAudit: parseIntEnv(source, 'X402_TRIAL_LIMIT_GEO_AUDIT', 2, { min: 0, max: 100 }),
      trialUsageTtlSeconds: parseIntEnv(source, 'X402_TRIAL_USAGE_TTL_S', 7 * 86400, { min: 86400 }),

      // =====================================================================
      // ANM-NATIVE SETTLEMENT LANE
      //
      // On Base we sponsor the gas for every settlement (~$0.0018 spent /
      // $0.0042 reserved measured), which puts a hard ~$0.0084 floor under
      // every USDC price here. On Animica the PAYER pays the fee from their
      // own balance and the gateway spends nothing, so that floor does not
      // exist on this lane. That is what funds the ANM discount below — it is
      // a real cost we avoid, not a promotion.
      //
      // NETWORK ID: `animica:1`, never `eip155:1`. This chain's chainId is 1,
      // and an agent that read `eip155:1` would try to pay on ETHEREUM
      // MAINNET and lose the money. The genesis hash is published with it.
      // =====================================================================
      anmLaneEnabled: envFrom(source, 'X402_ANM_ENABLED', '1') === '1',
      anmNetworkId: envFrom(source, 'X402_ANM_NETWORK_ID', 'animica:1'),
      anmChainId: parseIntEnv(source, 'X402_ANM_CHAIN_ID', 1, { min: 1 }),
      anmGenesisHash: envFrom(source, 'X402_ANM_GENESIS_HASH', '0xa0892158cf997c56e91d0aa12e60c36037dae34800a2b54111a8fa17ec88b7de'),
      // Where ANM payments land. Defaults to the foundation treasury, read
      // from consensus/rewards.py — NEVER retype this from memory, the
      // elided forms in notes do not checksum.
      anmPayTo: envFrom(source, 'X402_ANM_PAY_TO', 'anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga'),
      // The "further discount for paying in ANM". Funded by the gas we do not
      // spend on this lane. Applied to the USD price before conversion.
      anmDiscountPercent: parseIntEnv(source, 'X402_ANM_DISCOUNT_PCT', 25, { min: 0, max: 90 }),
      anmSettleTimeoutMs: parseIntEnv(source, 'X402_ANM_SETTLE_TIMEOUT_MS', 45000, { min: 1000, max: 300000 }),
      anmPollIntervalMs: parseIntEnv(source, 'X402_ANM_POLL_INTERVAL_MS', 1500, { min: 250, max: 30000 }),
      anmMaxFutureBlocks: parseIntEnv(source, 'X402_ANM_MAX_FUTURE_BLOCKS', 10, { min: 0, max: 1000 }),
      // ANM/USD reference feed. A STALE feed refuses to quote rather than
      // guessing — the rule Animica Pay already learned the hard way.
      anmPricePath: envFrom(source, 'X402_ANM_PRICE_PATH', '/var/www/animica.org/anm-price.json'),
      anmPriceMaxAgeSeconds: parseIntEnv(source, 'X402_ANM_PRICE_MAX_AGE_S', 900, { min: 60, max: 86400 }),

      // =====================================================================
      // PREPAID CREDITS — one settlement, then N gas-free calls.
      // =====================================================================
      creditsEnabled: envFrom(source, 'X402_CREDITS_ENABLED', '1') === '1',
      creditsPriceUsd: priceOf('X402_CREDITS_PRICE_USDC', '0.50'),
      // The bonus IS the settlement gas we do not spend on calls 2..N.
      creditsBonusPct: parseIntEnv(source, 'X402_CREDITS_BONUS_PCT', 10, { min: 0, max: 100 }),
      creditsTtlDays: parseIntEnv(source, 'X402_CREDITS_TTL_DAYS', 365, { min: 1, max: 3650 }),

      // =====================================================================
      // ANM 402 SCAN — a public directory of x402 services that settle in
      // ANM. Anyone may register; every listing is PROBED, and a listing that
      // stops answering a real 402 is marked dead rather than quietly kept.
      // =====================================================================
      scanEnabled: envFrom(source, 'X402_SCAN_ENABLED', '1') === '1',
      scanProbeTimeoutMs: parseIntEnv(source, 'X402_SCAN_PROBE_TIMEOUT_MS', 10000, { min: 1000, max: 60000 }),
      scanRegisterPerHour: parseIntEnv(source, 'X402_SCAN_REGISTER_PER_HOUR', 10, { min: 1, max: 1000 }),
      scanRecheckIntervalMs: parseIntEnv(source, 'X402_SCAN_RECHECK_INTERVAL_MS', 3600000, { min: 60000 }),
      scanMaxServices: parseIntEnv(source, 'X402_SCAN_MAX_SERVICES', 10000, { min: 10 }),

      // =====================================================================
      // ANM ADOPTION BOUNTY — paid to operators who open THEIR products to
      // ANM-native x402. Denominated in USD and converted at the NonKYC rate
      // at claim time, so the incentive keeps its value as ANM moves.
      //
      // TWO GATES, both load-bearing:
      //  1. TREASURY SOLVENCY. A claim is only accepted when the treasury can
      //     actually cover it plus everything already reserved. The program
      //     stops accepting rather than promising what it cannot pay.
      //  2. HUMAN PAYOUT. This gateway holds no treasury key and never will.
      //     A verified claim is RESERVED; an operator signs the transfer.
      //     Auto-paying on a probe would be trivially farmable — anyone can
      //     serve a static 402 document.
      // =====================================================================
      bountyEnabled: envFrom(source, 'X402_BOUNTY_ENABLED', '1') === '1',
      // 'open'   = anyone may claim while the budget lasts
      // 'closed' = claims are accepted and verified but need explicit
      //            operator approval before they are reserved
      bountyMode: (envFrom(source, 'X402_BOUNTY_MODE', 'closed') === 'open') ? 'open' : 'closed',
      bountyAmountUsd: priceOf('X402_BOUNTY_AMOUNT_USD', '1.00'),
      bountyTreasuryAddress: envFrom(source, 'X402_BOUNTY_TREASURY', 'anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga'),
      // Keep a reserve so the bounty programme can never drain the treasury.
      bountyTreasuryReserveAnm: envFrom(source, 'X402_BOUNTY_TREASURY_RESERVE_ANM', '10000'),
      bountyMaxClaims: parseIntEnv(source, 'X402_BOUNTY_MAX_CLAIMS', 100, { min: 0, max: 100000 }),
      bountyOnePerDomain: envFrom(source, 'X402_BOUNTY_ONE_PER_DOMAIN', '1') === '1',

      // =====================================================================
      // NEW PRODUCTS
      // =====================================================================
      // Web fetch/extract — URL to clean text. SSRF rules are NOT optional
      // here: this endpoint takes an attacker-supplied URL by design.
      fetchEnabled: envFrom(source, 'X402_FETCH_ENABLED', '1') === '1',
      fetchPriceUsd: priceOf('X402_FETCH_PRICE_USDC', '0.004'),
      fetchTimeoutMs: parseIntEnv(source, 'X402_FETCH_TIMEOUT_MS', 15000, { min: 1000, max: 60000 }),
      fetchMaxBytes: parseIntEnv(source, 'X402_FETCH_MAX_BYTES', 2_000_000, { min: 1024 }),
      fetchMaxRedirects: parseIntEnv(source, 'X402_FETCH_MAX_REDIRECTS', 3, { min: 0, max: 10 }),

      // GEO / agent-legibility audit — is this site readable and citable by AI
      // agents at all? Deterministic: it fetches a handful of well-known paths
      // and re-probes the homepage as each named AI crawler. No inference.
      geoAuditEnabled: envFrom(source, 'X402_GEO_AUDIT_ENABLED', '1') === '1',
      // Static fallback only, used until the first dynamic-pricing tick; the live
      // price is 7x the Base settlement floor (see DYN_MULT).
      geoAuditPriceUsd: priceOf('X402_GEO_AUDIT_PRICE_USDC', '0.006'),
      // Per-request timeout. A slow origin must not hold a connection open for
      // the whole budget on one probe.
      geoAuditTimeoutMs: parseIntEnv(source, 'X402_GEO_AUDIT_TIMEOUT_MS', 12000, { min: 1000, max: 60000 }),
      // Whole-audit wall clock. ~16 requests go out; without a ceiling a tarpit
      // origin turns one paid call into a held worker.
      geoAuditBudgetMs: parseIntEnv(source, 'X402_GEO_AUDIT_BUDGET_MS', 45000, { min: 5000, max: 180000 }),
      geoAuditMaxBytes: parseIntEnv(source, 'X402_GEO_AUDIT_MAX_BYTES', 1_500_000, { min: 1024 }),
      // How many probes may be in flight against ONE origin. Auditing a site is
      // not a licence to hammer it.
      geoAuditConcurrency: parseIntEnv(source, 'X402_GEO_AUDIT_CONCURRENCY', 4, { min: 1, max: 12 }),

      // GEO fix — emits deployable llms.txt / robots.txt / JSON-LD. Costs more
      // than the audit: it verifies every link it will publish, then makes one
      // fenced model call for the prose.
      geoFixEnabled: envFrom(source, 'X402_GEO_FIX_ENABLED', '1') === '1',
      geoFixPriceUsd: priceOf('X402_GEO_FIX_PRICE_USDC', '0.007'),
      geoFixBudgetMs: parseIntEnv(source, 'X402_GEO_FIX_BUDGET_MS', 90000, { min: 5000, max: 240000 }),
      geoFixDefaultLinks: parseIntEnv(source, 'X402_GEO_FIX_DEFAULT_LINKS', 20, { min: 1, max: 100 }),
      geoFixMaxLinks: parseIntEnv(source, 'X402_GEO_FIX_MAX_LINKS', 40, { min: 1, max: 200 }),

      // Paid Crawl — website owners charge AI crawlers for access.
      // FREE FOR SITE OPERATORS BY DESIGN: registration, the decision
      // endpoint, verification and earnings are all unpaid routes. Revenue
      // comes from the crawler buying a pass, never from the site. Do not
      // add a price knob for the operator side without re-reading why.
      crawlEnabled: envFrom(source, 'X402_CRAWL_ENABLED', '1') === '1',
      // The site keeps this share of every billed crawl; the rest is the
      // gateway fee. 9000 bps = the "you keep 90%" on the public page, so
      // changing it changes a published promise.
      crawlOperatorShareBps: parseIntEnv(source, 'X402_CRAWL_OPERATOR_SHARE_BPS', 9000, { min: 0, max: 10000 }),
      crawlPassPriceUsd: priceOf('X402_CRAWL_PASS_PRICE_USDC', '0.010'),
      // Forward-confirmed reverse DNS is what separates a real Googlebot from
      // a forged one. Disabling it makes every search-crawler claim
      // unverifiable, which means spoofs inherit the free lane — only turn it
      // off if this host cannot do outbound DNS at all.
      crawlVerifyRdns: envFrom(source, 'X402_CRAWL_VERIFY_RDNS', '1') === '1',
      // Unknown-User-Agent triage runs on AICF workers (who are paid in ANM
      // for it) and is advisory only — see products/crawl-triage.js.
      crawlTriageEnabled: envFrom(source, 'X402_CRAWL_TRIAGE_ENABLED', '1') === '1',
      crawlTriageIntervalMs: parseIntEnv(source, 'X402_CRAWL_TRIAGE_INTERVAL_MS', 15 * 60 * 1000, { min: 60_000 }),
      crawlTriageBatch: parseIntEnv(source, 'X402_CRAWL_TRIAGE_BATCH', 10, { min: 1, max: 100 }),
      // Post-quantum crawl licences (ML-DSA-65). The key is generated once and
      // persisted 0600: a per-boot key would silently invalidate every licence
      // issued before the last restart, which is the one thing a provenance
      // receipt may never do.
      crawlLicenceKeyPath: envFrom(source, 'X402_CRAWL_LICENCE_KEY', ''),
      crawlLicenceConcurrency: parseIntEnv(source, 'X402_CRAWL_LICENCE_CONCURRENCY', 2, { min: 1, max: 16 }),

      // x402 Mesh — a merged, scored index of the whole x402 economy
      // (Coinbase Bazaar + 402index), answering who an agent should buy from.
      meshEnabled: envFrom(source, 'X402_MESH_ENABLED', '1') === '1',
      meshFindPriceUsd: priceOf('X402_MESH_FIND_PRICE_USDC', '0.006'),
      // The index is harvested once and cached; a search must never fan out
      // into 150 upstream requests.
      meshCacheTtlMs: parseIntEnv(source, 'X402_MESH_CACHE_TTL_MS', 6 * 3600 * 1000, { min: 60_000 }),
      meshFetchTimeoutMs: parseIntEnv(source, 'X402_MESH_FETCH_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),
      // Pages per directory. 200 x 100 covers Bazaar's ~15k whole; 402index is
      // ~95k and deliberately truncated — its long tail is unpriced and
      // unschema'd, so the marginal row adds a name and nothing actionable.
      meshMaxPages: parseIntEnv(source, 'X402_MESH_MAX_PAGES', 200, { min: 1, max: 2000 }),
      meshMaxResults: parseIntEnv(source, 'X402_MESH_MAX_RESULTS', 50, { min: 1, max: 200 }),
      // Pause between directory pages. Harvesting 200 pages back-to-back got us
      // a 429 from 402index — we were the impolite client. The index has a
      // multi-hour TTL, so a slower build costs nothing and stops us being
      // rate-limited into an incomplete picture.
      meshDirectoryPageDelayMs: parseIntEnv(source, 'X402_MESH_PAGE_DELAY_MS', 300, { min: 0, max: 10000 }),
      meshDirectoryRetries: parseIntEnv(source, 'X402_MESH_PAGE_RETRIES', 2, { min: 0, max: 6 }),
      // 402index is rate-limited per minute and its long tail is thin data —
      // 19% priced, 0% with schemas — while Bazaar is fully priced. So we take
      // a smaller slice of it far more slowly. Our own probe harvester now
      // produces better facts than either directory publishes anyway.
      meshIndex402MaxPages: parseIntEnv(source, 'X402_MESH_402INDEX_MAX_PAGES', 50, { min: 0, max: 1000 }),
      meshIndex402PageDelayMs: parseIntEnv(source, 'X402_MESH_402INDEX_PAGE_DELAY_MS', 1200, { min: 0, max: 30000 }),

      // x402 Solve — compile a goal into a priced plan of real calls. PLANS
      // ONLY: it never spends. Execution would mean holding a funded wallet and
      // paying strangers inside a model-driven loop, which is an explicit
      // decision with a hard cap behind it, not a default.
      solveEnabled: envFrom(source, 'X402_SOLVE_ENABLED', '1') === '1',
      solvePriceUsd: priceOf('X402_SOLVE_PRICE_USDC', '0.008'),
      solveMaxSteps: parseIntEnv(source, 'X402_SOLVE_MAX_STEPS', 6, { min: 1, max: 12 }),
      solveDefaultBudgetUsd: envFrom(source, 'X402_SOLVE_DEFAULT_BUDGET_USD', '1.00'),
      solveMaxBudgetUsd: envFrom(source, 'X402_SOLVE_MAX_BUDGET_USD', '100'),
      // Minimum share of a capability's words a candidate must actually cover
      // before it can be planned. Without this the planner answered "verify
      // company legitimacy" with an email-address validator.
      solveMinCoverage: Number(envFrom(source, 'X402_SOLVE_MIN_COVERAGE', '0.5')),

      // ---------------------------------------------------------------------
      // x402 ANALYTICS ENGINE — statistics over the same merged index the Mesh
      // maintains, with the interpretation written by AICF.
      //
      // Prices sit at the same multiple as the other index-backed products:
      // the work is arithmetic over a cached index plus at most one model call,
      // and these are meant to be bought before a pricing or purchase decision
      // rather than in volume.
      analyticsEnabled: envFrom(source, 'X402_ANALYTICS_ENABLED', '1') === '1',
      analyticsMarketPriceUsd: priceOf('X402_ANALYTICS_MARKET_PRICE_USDC', '0.008'),
      analyticsPricePriceUsd: priceOf('X402_ANALYTICS_PRICE_PRICE_USDC', '0.008'),
      analyticsPeersPriceUsd: priceOf('X402_ANALYTICS_PEERS_PRICE_USDC', '0.008'),
      // Share of the query's distinct words a listing must actually contain
      // before it counts as part of a segment. Same floor, and the same
      // reasoning, as the solve planner: BM25 always returns a best match, and
      // a distribution computed over "best of 31,000" is not a distribution of
      // anything a buyer asked about.
      analyticsMinCoverage: Number(envFrom(source, 'X402_ANALYTICS_MIN_COVERAGE', '0.5')),
      // Below this many comparables the endpoints REFUSE to compute a
      // percentile and say why. A percentile over four rows renders identically
      // to a percentile over four thousand, and merchants price against it.
      analyticsMinComparables: parseIntEnv(source, 'X402_ANALYTICS_MIN_COMPARABLES', 8, { min: 2, max: 1000 }),
      // Trend history: at most one observation per segment per interval, so a
      // popular segment cannot turn the history table into a request log.
      analyticsSnapshotMinIntervalMs: parseIntEnv(source, 'X402_ANALYTICS_SNAPSHOT_MIN_INTERVAL_MS', 3600_000, { min: 0 }),
      analyticsHistoryLimit: parseIntEnv(source, 'X402_ANALYTICS_HISTORY_LIMIT', 500, { min: 2, max: 5000 }),

      // ---------------------------------------------------------------------
      // AICF — Animica's OWN inference network, as distinct from the pool /v1
      // that every other product here calls.
      //
      // THE DISTINCTION IS THE POINT. cfg.utilityInferenceUrl points at the
      // pool API, which maps `anm-fast-8b` onto a local ollama process on this
      // box. AICF is the on-chain fabric: the node queues a job, a registered
      // worker claims it, and that worker is PAID IN ANM for serving it. A
      // product that advertises Animica's own inference network has to mean the
      // second thing.
      //
      // The bridge in front of AICF falls back to the pool when no worker
      // claims a job, and reports that by naming the model that actually
      // answered instead of echoing the one requested. `createAicfEngine`
      // reads exactly that signal and reports provenance per call, so a buyer
      // is never told AICF served a request that it did not. Measured on this
      // host 2026-08-19: a request for `animica-chat` came back as
      // `anm-fast-8b`, i.e. the fallback served it.
      aicfEnabled: envFrom(source, 'X402_AICF_ENABLED', '1') === '1',
      aicfUrl: envFrom(source, 'X402_AICF_URL', 'http://127.0.0.1:4600/v1/chat/completions'),
      aicfHealthUrl: envFrom(source, 'X402_AICF_HEALTH_URL', 'http://127.0.0.1:4600/v1/models'),
      // The NETWORK model, not a pool catalog id. Requesting a pool id here
      // would make the provenance check meaningless, since a fallback answer
      // would echo it back and look like an AICF serve.
      aicfModel: envFrom(source, 'X402_AICF_MODEL', 'animica-chat'),
      aicfKey: envFrom(source, 'X402_AICF_KEY', ''),
      // AICF is a job queue with a claim step, not a local socket: a worker
      // claiming and then loading a model routinely takes tens of seconds. A
      // short timeout here would guarantee we only ever see the fallback.
      aicfTimeoutMs: parseIntEnv(source, 'X402_AICF_TIMEOUT_MS', 150_000, { min: 1000, max: 600_000 }),
      aicfMaxTokens: parseIntEnv(source, 'X402_AICF_MAX_TOKENS', 400, { min: 32, max: 4096 }),
      // What the bridge falls back to, used ONLY to detect the one ambiguous
      // case: aicfModel configured to the same string the bridge falls back to,
      // which is reported as indeterminate rather than resolved in our favour.
      aicfFallbackModelHint: envFrom(source, 'X402_AICF_FALLBACK_MODEL_HINT', 'anm-fast-8b'),

      // ---------------------------------------------------------------------
      // OUTBOUND SPENDING (POST /x402/buy). Off by default, and off unless a
      // DEDICATED key is configured. The address that settles our incoming
      // payments is passed separately purely so the payer can REFUSE to be it:
      // one confused purchase must not be able to drain the float every
      // product settles through.
      // ---------------------------------------------------------------------
      execEnabled: envFrom(source, 'X402_EXEC_ENABLED', '0') === '1',
      execPrivateKey: envFrom(source, 'X402_EXEC_PRIVATE_KEY', ''),
      execFeeUsd: priceOf('X402_EXEC_FEE_USDC', '0.01'),
      execTimeoutMs: parseIntEnv(source, 'X402_EXEC_TIMEOUT_MS', 30000, { min: 1000, max: 120000 }),
      // Deliberately small. These are the numbers that decide how much a bug
      // can cost, so they start where a bug is affordable.
      execMaxPerCallUsd: Number(envFrom(source, 'X402_EXEC_MAX_PER_CALL_USD', '0.10')),
      execMaxPerDayUsd: Number(envFrom(source, 'X402_EXEC_MAX_PER_DAY_USD', '1.00')),

      // The schema harvester: probe indexed resources WITHOUT paying, so a 402
      // becomes the merchant's own statement of price and request shape.
      // Background work (index warm-up at boot, periodic probe sweeps). Tests
      // turn this OFF: leaving it on made every test gateway fire a real
      // harvest against two third-party directories, which is slow, flaky and
      // rude to them.
      meshBackgroundEnabled: envFrom(source, 'X402_MESH_BACKGROUND', '1') === '1',
      meshHarvestEnabled: envFrom(source, 'X402_MESH_HARVEST_ENABLED', '1') === '1',
      meshProbePriceUsd: priceOf('X402_MESH_PROBE_PRICE_USDC', '0.005'),
      meshProbeTimeoutMs: parseIntEnv(source, 'X402_MESH_PROBE_TIMEOUT_MS', 12000, { min: 1000, max: 60000 }),
      meshProbeUserAgent: envFrom(source, 'X402_MESH_PROBE_UA',
        'AnimicaMeshProbe/1.0 (+https://animica.dev/x402/mesh/find; unpaid discovery probe, no side effects)'),
      // Serial per host with a gap between probes. One merchant listing forty
      // endpoints must not receive forty simultaneous requests from us.
      meshProbeHostDelayMs: parseIntEnv(source, 'X402_MESH_PROBE_HOST_DELAY_MS', 1500, { min: 0, max: 60000 }),
      // Re-probe cadence. Prices and schemas change slowly; hammering does not
      // make them change faster.
      meshProbeTtlMs: parseIntEnv(source, 'X402_MESH_PROBE_TTL_MS', 7 * 86400 * 1000, { min: 60_000 }),
      // A sweep is a slow background sip, not a crawl: serving traffic wins.
      meshSweepIntervalMs: parseIntEnv(source, 'X402_MESH_SWEEP_INTERVAL_MS', 15 * 60 * 1000, { min: 30_000 }),
      meshSweepBudgetMs: parseIntEnv(source, 'X402_MESH_SWEEP_BUDGET_MS', 5 * 60 * 1000, { min: 5_000 }),
      meshSweepMaxProbes: parseIntEnv(source, 'X402_MESH_SWEEP_MAX_PROBES', 400, { min: 1, max: 20000 }),
      meshSweepConcurrency: parseIntEnv(source, 'X402_MESH_SWEEP_CONCURRENCY', 8, { min: 1, max: 64 }),

      // Batch embeddings via the local all-MiniLM-L6-v2 in the deploy indexer.
      embedEnabled: envFrom(source, 'X402_EMBED_ENABLED', '1') === '1',
      embedPriceUsd: priceOf('X402_EMBED_PRICE_USDC', '0.004'),
      embedUrl: envFrom(source, 'X402_EMBED_URL', 'http://127.0.0.1:4630'),
      embedMaxTexts: parseIntEnv(source, 'X402_EMBED_MAX_TEXTS', 256, { min: 1, max: 4096 }),
      embedMaxCharsPerText: parseIntEnv(source, 'X402_EMBED_MAX_CHARS', 8192, { min: 64 }),
      embedTimeoutMs: parseIntEnv(source, 'X402_EMBED_TIMEOUT_MS', 30000, { min: 1000, max: 120000 }),

      // Ask-a-URL: one-shot RAG over a single page (fetch -> chunk -> embed
      // -> retrieve -> answer). Nothing is stored.
      askUrlEnabled: envFrom(source, 'X402_ASK_URL_ENABLED', '1') === '1',
      askUrlPriceUsd: priceOf('X402_ASK_URL_PRICE_USDC', '0.008'),
      askUrlInferenceUrl: envFrom(source, 'X402_ASK_URL_INFERENCE_URL', 'http://127.0.0.1:4000/v1'),
      askUrlApiKey: envFrom(source, 'X402_ASK_URL_API_KEY', ''),
      askUrlModel: envFrom(source, 'X402_ASK_URL_MODEL', 'anm-fast-8b'),
      askUrlTimeoutMs: parseIntEnv(source, 'X402_ASK_URL_TIMEOUT_MS', 45000, { min: 1000, max: 120000 }),
      askUrlMaxTokens: parseIntEnv(source, 'X402_ASK_URL_MAX_TOKENS', 600, { min: 32, max: 4096 }),
      askUrlChunkChars: parseIntEnv(source, 'X402_ASK_URL_CHUNK_CHARS', 1200, { min: 200, max: 8000 }),
      // Overlap so a fact straddling a chunk boundary stays retrievable.
      askUrlChunkOverlap: parseIntEnv(source, 'X402_ASK_URL_CHUNK_OVERLAP', 200, { min: 0, max: 2000 }),
      askUrlMaxChunks: parseIntEnv(source, 'X402_ASK_URL_MAX_CHUNKS', 120, { min: 1, max: 1000 }),
      askUrlTopK: parseIntEnv(source, 'X402_ASK_URL_TOP_K', 4, { min: 1, max: 20 }),
      // Below this similarity we DECLINE rather than let the model improvise.
      // 0.25 matches the floor the Deploy product settled on after 0.30
      // rejected a legitimate single-chunk page.
      askUrlMinScore: envFrom(source, 'X402_ASK_URL_MIN_SCORE', '0.25'),

      // On-chain notarisation: anchor a digest, get a proof.
      notarizeEnabled: envFrom(source, 'X402_NOTARIZE_ENABLED', '1') === '1',
      notarizePriceUsd: priceOf('X402_NOTARIZE_PRICE_USDC', '0.006'),
      notarizeNamespace: envFrom(source, 'X402_NOTARIZE_NAMESPACE', 'x402-notary'),
      notarizeTimeoutMs: parseIntEnv(source, 'X402_NOTARIZE_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),

      // Addressable blob storage over the same DA layer.
      blobEnabled: envFrom(source, 'X402_BLOB_ENABLED', '1') === '1',
      blobPriceUsd: priceOf('X402_BLOB_PRICE_USDC', '0.006'),
      blobNamespace: envFrom(source, 'X402_BLOB_NAMESPACE', 'x402-blobs'),
      blobMaxBytes: parseIntEnv(source, 'X402_BLOB_MAX_BYTES', 1048576, { min: 1024, max: 8388608 }),
      // Refuse new writes while the DA volume is nearly full: taking payment
      // to store something we may fail to write is exactly the case the
      // codebase's own rule forbids.
      blobMinFreeBytes: envFrom(source, 'X402_BLOB_MIN_FREE_BYTES', '1073741824'),

      // Notarised forecasts. The ANCHOR is the product: if the record cannot
      // be committed the sale does not happen, so this shares the DA gate.
      forecastEnabled: envFrom(source, 'X402_FORECAST_ENABLED', '1') === '1',
      forecastPriceUsd: priceOf('X402_FORECAST_PRICE_USDC', '0.009'),
      forecastNamespace: envFrom(source, 'X402_FORECAST_NAMESPACE', 'x402-forecast'),
      forecastTimeoutMs: parseIntEnv(source, 'X402_FORECAST_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),
      forecastMarketTimeoutMs: parseIntEnv(source, 'X402_FORECAST_MARKET_TIMEOUT_MS', 10000, { min: 1000, max: 60000 }),
      forecastInferenceUrl: envFrom(source, 'X402_FORECAST_INFERENCE_URL', 'http://127.0.0.1:4000/v1/chat/completions'),
      forecastInferenceKey: envFrom(source, 'X402_FORECAST_INFERENCE_KEY', envFrom(source, 'X402_ASK_URL_API_KEY', '')),
      forecastModel: envFrom(source, 'X402_FORECAST_MODEL', 'anm-fast-8b'),
      forecastInferenceTimeoutMs: parseIntEnv(source, 'X402_FORECAST_INFERENCE_TIMEOUT_MS', 45000, { min: 1000, max: 180000 }),
      // Background scoring sweep: markets settle, so forecasts get graded.
      forecastResolveIntervalMs: parseIntEnv(source, 'X402_FORECAST_RESOLVE_INTERVAL_MS', 1800000, { min: 60000 }),
      // Below this, report NO market rather than pair a forecast with a market
      // about something else — a wrong pairing in a permanent record is worse
      // than no pairing at all.
      forecastMinRelevance: envFrom(source, 'X402_FORECAST_MIN_RELEVANCE', '0.45'),

      // =====================================================================
      // ANIMICA EXECUTE — the flagship: pay once, get a verified result.
      // Built on capabilities already proven here. Verification is honest
      // about having ONE inference backend; see execute.js.
      // =====================================================================
      executeEnabled: envFrom(source, 'X402_EXECUTE_ENABLED', '1') === '1',
      executePriceUsd: priceOf('X402_EXECUTE_PRICE_USDC', '0.02'),
      executeNamespace: envFrom(source, 'X402_EXECUTE_NAMESPACE', 'x402-execute'),
      executeInferenceUrl: envFrom(source, 'X402_EXECUTE_INFERENCE_URL', 'http://127.0.0.1:4000/v1/chat/completions'),
      executeHealthUrl: envFrom(source, 'X402_EXECUTE_HEALTH_URL', 'http://127.0.0.1:4000/v1/models'),
      executeInferenceKey: envFrom(source, 'X402_EXECUTE_INFERENCE_KEY', envFrom(source, 'X402_ASK_URL_API_KEY', '')),
      executeModel: envFrom(source, 'X402_EXECUTE_MODEL', 'anm-fast-8b'),
      executeInferenceTimeoutMs: parseIntEnv(source, 'X402_EXECUTE_INFERENCE_TIMEOUT_MS', 60000, { min: 1000, max: 300000 }),
      executeTimeoutMs: parseIntEnv(source, 'X402_EXECUTE_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),
      executeMaxTokens: parseIntEnv(source, 'X402_EXECUTE_MAX_TOKENS', 900, { min: 32, max: 8192 }),
      executeMaxTaskChars: parseIntEnv(source, 'X402_EXECUTE_MAX_TASK_CHARS', 4000, { min: 16 }),
      executeContextChars: parseIntEnv(source, 'X402_EXECUTE_CONTEXT_CHARS', 12000, { min: 500 }),
      // Samples taken at quality:"verified". More samples measure stability
      // better but cost real GPU seconds, so this is bounded.
      executeVerifiedSamples: parseIntEnv(source, 'X402_EXECUTE_VERIFIED_SAMPLES', 3, { min: 2, max: 7 }),
      executeSignTimeoutMs: parseIntEnv(source, 'X402_EXECUTE_SIGN_TIMEOUT_MS', 15000, { min: 1000 }),
      executeMaxConcurrentSign: parseIntEnv(source, 'X402_EXECUTE_MAX_CONCURRENT_SIGN', 4, { min: 1, max: 32 }),
      // Optional deterministic receipt key. Unset => a fresh keypair per
      // receipt, which still proves integrity of THAT receipt but gives no
      // continuity of identity across receipts; the response ships the public
      // key either way so a verifier is never left guessing.
      executeSignSeed: envFrom(source, 'X402_EXECUTE_SIGN_SEED', ''),

      // Free web crawler (animica.dev/crawl) — replaces the agent swarm.
      // Free because it costs us one fetch and one short model call, and it
      // is the honest demonstration of the paid fetch/ask products.
      freeCrawlEnabled: envFrom(source, 'X402_FREE_CRAWL_ENABLED', '1') === '1',
      freeCrawlPerDay: parseIntEnv(source, 'X402_FREE_CRAWL_PER_DAY', 25, { min: 0, max: 10000 }),
      freeCrawlMaxChars: parseIntEnv(source, 'X402_FREE_CRAWL_MAX_CHARS', 20000, { min: 500 }),
      freeCrawlMaxChunks: parseIntEnv(source, 'X402_FREE_CRAWL_MAX_CHUNKS', 80, { min: 1, max: 500 }),
      freeCrawlTopK: parseIntEnv(source, 'X402_FREE_CRAWL_TOP_K', 4, { min: 1, max: 10 }),
      freeCrawlMaxTokens: parseIntEnv(source, 'X402_FREE_CRAWL_MAX_TOKENS', 500, { min: 32, max: 4096 }),

      // =====================================================================
      // AGENT UTILITY API — the cheap cognition layer (extract/classify/
      // entities/json-repair/injection/rerank/route). Small-model work whose
      // product is the GUARANTEED OUTPUT SHAPE, not the model.
      //
      // A per-call price cannot go below the Base settlement floor, so
      // high-volume use is meant to run on prepaid credits (no gas per call)
      // or the ANM lane (payer pays their own fee). See utility.js.
      // =====================================================================
      utilityEnabled: envFrom(source, 'X402_UTILITY_ENABLED', '1') === '1',
      utilityPriceUsd: priceOf('X402_UTILITY_PRICE_USDC', '0.003'),
      utilityInferenceUrl: envFrom(source, 'X402_UTILITY_INFERENCE_URL', 'http://127.0.0.1:4000/v1/chat/completions'),
      utilityHealthUrl: envFrom(source, 'X402_UTILITY_HEALTH_URL', 'http://127.0.0.1:4000/v1/models'),
      utilityInferenceKey: envFrom(source, 'X402_UTILITY_INFERENCE_KEY', envFrom(source, 'X402_ASK_URL_API_KEY', '')),
      utilityModel: envFrom(source, 'X402_UTILITY_MODEL', 'anm-fast-8b'),
      utilityTimeoutMs: parseIntEnv(source, 'X402_UTILITY_TIMEOUT_MS', 45000, { min: 1000, max: 180000 }),
      utilityMaxTokens: parseIntEnv(source, 'X402_UTILITY_MAX_TOKENS', 1200, { min: 64, max: 8192 }),
      utilityMaxInputChars: parseIntEnv(source, 'X402_UTILITY_MAX_INPUT_CHARS', 40000, { min: 100 }),

      // Post-quantum signature verification (ML-DSA-65 and friends).
      pqEnabled: envFrom(source, 'X402_PQ_ENABLED', '1') === '1',
      pqVerifyPriceUsd: priceOf('X402_PQ_VERIFY_PRICE_USDC', '0.004'),
      pqMaxMessageBytes: parseIntEnv(source, 'X402_PQ_MAX_MESSAGE_BYTES', 1_000_000, { min: 32 }),
      // The verifier is Python. Each call spawns the repo venv with a FIXED
      // script and JSON on stdin — never argv, never a shell.
      pqPythonBin: envFrom(source, 'X402_PQ_PYTHON', '/root/animica/.venv/bin/python'),
      pqPythonPath: envFrom(source, 'X402_PQ_PYTHONPATH', '/root/animica/python'),
      pqTimeoutMs: parseIntEnv(source, 'X402_PQ_TIMEOUT_MS', 10000, { min: 500, max: 60000 }),
      // "One process per request" is a DoS primitive without a ceiling.
      pqMaxConcurrent: parseIntEnv(source, 'X402_PQ_MAX_CONCURRENT', 4, { min: 1, max: 64 }),

      // Signed ANM price attestation.
      oracleEnabled: envFrom(source, 'X402_ORACLE_ENABLED', '1') === '1',
      oraclePriceUsd: priceOf('X402_ORACLE_PRICE_USDC', '0.004'),
      // OPTIONAL secp256k1 key for price attestations. Unset => attestations
      // are returned UNSIGNED and say so; we never claim an attestation we
      // cannot actually make (same rule as the randomness attested:false).
      oraclePrivateKey: envFrom(source, 'X402_ORACLE_PRIVATE_KEY', ''),

      // Holder snapshot / rich list for airdrops.
      snapshotEnabled: envFrom(source, 'X402_SNAPSHOT_ENABLED', '1') === '1',
      snapshotPriceUsd: priceOf('X402_SNAPSHOT_PRICE_USDC', '0.007'),
      snapshotMaxHolders: parseIntEnv(source, 'X402_SNAPSHOT_MAX_HOLDERS', 1000, { min: 1, max: 100000 }),

      // Mempool / network telemetry feed.
      mempoolEnabled: envFrom(source, 'X402_MEMPOOL_ENABLED', '1') === '1',
      mempoolPriceUsd: priceOf('X402_MEMPOOL_PRICE_USDC', '0.004'),

      // Block-reward share leases, sold against the TREASURY's 25% of every
      // block (50/25/25 miner/treasury/inference after the 75,000 fork).
      // DISABLED BY DEFAULT: a paid share of future block rewards is more
      // securities-flavoured than a spot sale, not less, and that is an
      // operator decision rather than a default.
      leaseEnabled: envFrom(source, 'X402_LEASE_ENABLED', '0') === '1',
      leasePriceUsd: priceOf('X402_LEASE_PRICE_USDC', '0.50'),
      leaseDiscountPercent: parseIntEnv(source, 'X402_LEASE_DISCOUNT_PCT', 10, { min: 0, max: 90 }),
      leaseTreasurySharePct: parseIntEnv(source, 'X402_LEASE_TREASURY_SHARE_PCT', 25, { min: 1, max: 100 }),
      // Hard ceiling on how much of the treasury's own share may be sold
      // across ALL overlapping leases. Oversubscription must be impossible.
      leaseMaxSoldPct: parseIntEnv(source, 'X402_LEASE_MAX_SOLD_PCT', 50, { min: 1, max: 100 }),
      // Average ANM the treasury receives per block. Default 75 = 25% of the
      // 300 ANM subsidy after the 75,000 fork (50/25/25 miner/treasury/
      // inference). VERIFY THIS before enabling the product: it is the basis
      // of every quote, and the chain does not expose the subsidy over RPC.
      leaseTreasuryAnmPerBlock: envFrom(source, 'X402_LEASE_TREASURY_ANM_PER_BLOCK', '75'),
      leaseMinBlocks: parseIntEnv(source, 'X402_LEASE_MIN_BLOCKS', 100, { min: 1 }),
      leaseMaxBlocks: parseIntEnv(source, 'X402_LEASE_MAX_BLOCKS', 10000, { min: 1 }),

      // Clean egress. DISABLED BY DEFAULT: abuse lands on our IPs and our
      // abuse desk, which is an operator decision.
      dvpnEnabled: envFrom(source, 'X402_DVPN_ENABLED', '0') === '1',
      dvpnPriceUsd: priceOf('X402_DVPN_PRICE_USDC', '0.05'),

      // =====================================================================
      // TREASURY RECYCLER — x402 USDC revenue -> ANM.
      //
      // DISABLED and KEYLESS by default. It never reuses the facilitator's
      // private key; without its own key it can plan but cannot move funds.
      //
      // X402_RECYCLE_DEPOSIT_NETWORK has NO DEFAULT on purpose. EVM addresses
      // are identical across chains, so a Base transfer to a BEP20 deposit
      // address confirms and is never credited — silent, unrecoverable loss.
      // The operator must state the network, and it must match the chain.
      // =====================================================================
      recycleEnabled: envFrom(source, 'X402_RECYCLE_ENABLED', '0') === '1',
      recycleChainId: parseIntEnv(source, 'X402_RECYCLE_CHAIN_ID', 8453, { min: 1 }),
      recycleSourceAddress: envFrom(source, 'X402_RECYCLE_SOURCE', envFrom(source, 'X402_BASE_PAYTO', '')),
      recycleDepositAddress: envFrom(source, 'X402_RECYCLE_DEPOSIT_ADDRESS', ''),
      recycleDepositNetwork: envFrom(source, 'X402_RECYCLE_DEPOSIT_NETWORK', ''),
      recycleHasKey: envFrom(source, 'X402_RECYCLE_PRIVATE_KEY', '') !== '',
      // Leave enough USDC behind to cover in-flight refunds/incidents.
      recycleReserveAtomic: envFrom(source, 'X402_RECYCLE_RESERVE_ATOMIC', '0'),
      // The exchange minimum is 1 USDC.
      recycleMinDepositAtomic: envFrom(source, 'X402_RECYCLE_MIN_DEPOSIT_ATOMIC', '1000000'),
      recycleMaxPerRunAtomic: envFrom(source, 'X402_RECYCLE_MAX_PER_RUN_ATOMIC', '50000000'),
      // Until a first deposit is confirmed credited, every run is capped here.
      recycleDepositConfirmed: envFrom(source, 'X402_RECYCLE_DEPOSIT_CONFIRMED', '0') === '1',
      recycleTestAmountAtomic: envFrom(source, 'X402_RECYCLE_TEST_ATOMIC', '1000000'),
      recycleMarket: envFrom(source, 'X402_RECYCLE_MARKET', 'ANM_USDT'),
      recycleApiBase: envFrom(source, 'X402_RECYCLE_API_BASE', 'https://api.nonkyc.io/api/v2'),
      recycleApiKey: envFrom(source, 'X402_RECYCLE_API_KEY', ''),
      recycleApiSecret: envFrom(source, 'X402_RECYCLE_API_SECRET', ''),
    });
  } catch (e) {
    problems.push(e.message);
  }
  // The index walker deliberately stops at head - headMargin, so the lag it
  // reports can never drop below headMargin. A freshness gate at or below
  // that floor would make the history product permanently unavailable —
  // refuse the footgun at startup instead of at 3am.
  if (cfg && cfg.chainIndexMaxLagBlocks <= cfg.chainIndexHeadMargin) {
    problems.push(
      `X402_CHAIN_INDEX_MAX_LAG_BLOCKS (${cfg.chainIndexMaxLagBlocks}) must exceed X402_CHAIN_INDEX_HEAD_MARGIN (${cfg.chainIndexHeadMargin}); the index never indexes above head - margin, so the gate could never open`
    );
  }
  // In mode=self the offers this gateway signs must name the same chain the
  // built-in facilitator settles on. A mismatch (e.g. Sepolia offers against
  // a mainnet facilitator) is not a degraded mode: every payment would be
  // verified against the wrong chain id, so refuse to start.
  if (cfg && cfg.facilitatorMode === 'self') {
    const facNet = EVM_NETWORKS[envFrom(source, 'X402_NETWORK', 'base')];
    if (facNet && cfg.networkEvm !== facNet.caip2) {
      problems.push(
        `X402_NETWORK_EVM (${cfg.networkEvm}) contradicts the self-hosted facilitator's X402_NETWORK=${facNet.slug} (${facNet.caip2}); `
        + 'in mode=self both must name the same chain'
      );
    }
  }
  if (cfg && cfg.chainHistoryDefaultLimit > cfg.chainHistoryMaxLimit) {
    problems.push(
      `X402_CHAIN_HISTORY_DEFAULT_LIMIT (${cfg.chainHistoryDefaultLimit}) exceeds X402_CHAIN_HISTORY_MAX_LIMIT (${cfg.chainHistoryMaxLimit})`
    );
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
  loadTreasuryConfig,
  decimalToScaled,
  usdToUsdcAtomic,
  usdToTokenAtomic,
};
