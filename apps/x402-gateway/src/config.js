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
    evmFacilitatorPort,
    networkEvm,
    usdcAsset: env('X402_USDC_ASSET', USDC_DEFAULTS[networkEvm] || ''),
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
  // many units (see qrng/bulk: 10 independent draws, one settlement) — not to
  // shade the per-call number down.
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
      qrngPriceUsd: priceOf('X402_QRNG_PRICE_USDC', '0.01'),
      qrngMaxBytes: parseIntEnv(source, 'X402_QRNG_MAX_BYTES', 1024, { min: 1, max: 1048576 }),
      qrngTimeoutMs: parseIntEnv(source, 'X402_QRNG_TIMEOUT_MS', 5000, { min: 100, max: 60000 }),

      // P1b the randomness FAMILY (int/shuffle/pick/bulk/commit-reveal).
      // Every one of them derives from a single qrng draw, so they share the
      // qrng timeout/health gate; only the caps and prices live here.
      randomEnabled: envFrom(source, 'X402_RANDOM_ENABLED', '1') === '1',
      randomIntPriceUsd: priceOf('X402_RANDOM_INT_PRICE_USDC', '0.01'),
      randomShufflePriceUsd: priceOf('X402_RANDOM_SHUFFLE_PRICE_USDC', '0.01'),
      randomPickPriceUsd: priceOf('X402_RANDOM_PICK_PRICE_USDC', '0.01'),
      randomBulkPriceUsd: priceOf('X402_RANDOM_BULK_PRICE_USDC', '0.03'),
      randomCommitPriceUsd: priceOf('X402_RANDOM_COMMIT_PRICE_USDC', '0.01'),
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
      bulkChainPriceUsd: priceOf('X402_BULK_CHAIN_PRICE_USDC', '0.02'),
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
      chainHistoryPriceUsd: priceOf('X402_CHAIN_HISTORY_PRICE_USDC', '0.02'),
      chainHistoryMaxLimit: parseIntEnv(source, 'X402_CHAIN_HISTORY_MAX_LIMIT', 500, { min: 1, max: 10000 }),
      chainHistoryDefaultLimit: parseIntEnv(source, 'X402_CHAIN_HISTORY_DEFAULT_LIMIT', 100, { min: 1, max: 10000 }),

      // P5 bulk balances — measured ~5 ms/address batched, so 500/request.
      chainBalancesEnabled: envFrom(source, 'X402_CHAIN_BALANCES_ENABLED', '1') === '1',
      chainBalancesPriceUsd: priceOf('X402_CHAIN_BALANCES_PRICE_USDC', '0.01'),
      chainBalancesMaxAddresses: parseIntEnv(source, 'X402_CHAIN_BALANCES_MAX_ADDRESSES', 500, { min: 1, max: 5000 }),
      chainBalancesTimeoutMs: parseIntEnv(source, 'X402_CHAIN_BALANCES_TIMEOUT_MS', 20000, { min: 1000, max: 120000 }),

      // P3 priority inference — flagship LATER; hard-disabled by default and
      // additionally gated on live serving capacity (see src/capacity.js).
      priorityInferenceEnabled: envFrom(source, 'PRIORITY_INFERENCE_ENABLED', '0') === '1',
      priorityInferenceMinServingWorkers: parseIntEnv(source, 'PRIORITY_INFERENCE_MIN_SERVING_WORKERS', 2, { min: 1, max: 1000 }),
      inferencePriceUsd: priceOf('X402_INFERENCE_PRICE_USDC', '0.02'),
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
