// Central config + economic constants. All amounts are nANM (bigint) unless suffixed _ANM.

export const NANM_PER_ANM = 1_000_000_000n;

function env(name: string, fallback = ''): string {
  return process.env[name] ?? fallback;
}

export const config = {
  rpcUrl: env('ANIMICA_RPC_URL', 'http://127.0.0.1:8545/rpc'),
  freeV1: env('ANIMICA_FREE_V1', 'http://127.0.0.1:4600/v1'),
  poolV1: env('ANIMICA_POOL_V1', 'http://127.0.0.1:4000/v1'),
  poolKey: env('ANIMICA_POOL_KEY'),
  embedModel: env('ANIMICA_EMBED_MODEL', 'anm-embed'),
  mediaWorkerUrl: env('ANIMICA_MEDIA_WORKER_URL', 'http://127.0.0.1:4610'),

  cli: env('ANIMICA_CLI', '/root/animica/.venv/bin/animica'),
  walletsFile: env('ANIMICA_WALLETS_FILE', '/root/.animica/wallets.json'),
  treasuryLabel: env('MKT_TREASURY_LABEL', 'animica-marketplace'),
  treasuryAddress: env('MKT_TREASURY_ADDRESS'),

  // The Animica Foundation. .anm name-registration + renewal fees route here (the Animica
  // Internet's revenue → Foundation), as a ledger credit backed by the buyer's deposited ANM;
  // the Foundation withdraws its balance on-chain. Overridable via env.
  foundationAddress: env('MKT_FOUNDATION_ADDRESS',
    'anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga'),

  payoutEnabled: env('PAYOUT_ENABLED', '0') === '1',
  payoutMaxPerTxAnm: BigInt(env('PAYOUT_MAX_PER_TX_ANM', '5000')),
  payoutMaxPerDayAnm: BigInt(env('PAYOUT_MAX_PER_DAY_ANM', '20000')),
  payoutConfirmSecs: Number(env('PAYOUT_CONFIRM_SECS', '120')),
  finalityConfs: Number(env('TX_FINALITY_CONFIRMATIONS', '12')),

  feeBps: Number(env('MKT_FEE_BPS', '2000')), // 20%
  minWithdrawalNanm: BigInt(env('MIN_WITHDRAWAL_ANM', '1')) * NANM_PER_ANM,

  // App store (APP / DIGITAL_GOOD). FAIL-CLOSED: no default for either — the purchase-intent
  // route 503s until BOTH are configured. storeTreasuryAddress is a DEDICATED address used
  // ONLY for ANMSTORE1 purchases (the watcher's baseline reconciliation depends on that).
  storeTreasuryAddress: env('STORE_TREASURY_ADDRESS'),
  storeFeeBps: process.env.STORE_FEE_BPS != null ? Number(process.env.STORE_FEE_BPS) : NaN, // 3000 = 30% treasury / 70% creator
  storeDownloadSecret: env('STORE_DOWNLOAD_SECRET') || env('SESSION_SECRET', 'dev-insecure-secret'),

  sessionSecret: env('SESSION_SECRET', 'dev-insecure-secret'),
  baseUrl: env('PUBLIC_BASE_URL', 'https://animica.dev'),
  nodeId: env('ANIMICA_NODE_ID', 'animica.dev'), // this content-host's provider id (.anm hosting)
  adminToken: env('MKT_ADMIN_TOKEN', ''), // gates admin/seed endpoints; empty => disabled

  // ENA self-improvement: forward consented preference feedback to the training coordinator.
  enaCoordUrl: env('ENA_COORD_URL', 'http://127.0.0.1:8791'),
  enaApiToken: env('ENA_API_TOKEN', ''), // Bearer for the coordinator's gated /feedback route
};

export const CATEGORIES = [
  'Coding', 'Business', 'Research', 'Education', 'Marketing',
  'Finance', 'Science', 'Gaming', 'Personal', 'Enterprise',
] as const;

export const API_SCOPES = ['read', 'buy', 'use', 'publish', 'withdraw', 'names', 'message', 'host', 'vpn'] as const;

// Hosting rewards: IOU accrual rate for proven content availability (bytes-hours).
// Honest accounting — accrues as an IOU, NOT a spendable balance credit, until treasury-funded
// settlement (same model as AICF/media inference pay). Default ~0.5 ANM per GB-month.
export const HOSTING_NANM_PER_BYTE_HOUR = Number(env('HOSTING_NANM_PER_BYTE_HOUR', '0.0007'));
export const HOSTING_STALE_SECONDS = Number(env('HOSTING_STALE_SECONDS', '5400')); // 90 min no-heartbeat => inactive

// dVPN relay rewards: IOU accrual per reconciled byte. Same honest accounting as hosting — accrues
// as an IOU (VpnExit/VpnSession.rewardNanm), NOT a spendable balance, until treasury-funded
// settlement. Default ~0.5 nANM/byte ≈ 0.5 ANM per GB of reconciled traffic (per-GB price scale).
export const VPN_NANM_PER_BYTE = Number(env('VPN_NANM_PER_BYTE', '0.5'));
export const VPN_STALE_SECONDS = Number(env('VPN_STALE_SECONDS', '300')); // 5 min no-heartbeat => offline
export const VPN_DIVERGENCE_BPS = Number(env('VPN_DIVERGENCE_BPS', '1000')); // flag if |c-e|/max > 10%
// Anti-abuse accrual ceilings (nANM). Session cap bounds any single lease; the daily caps bound how
// much an exit / the whole network can accrue in a UTC day, so a spoofed receipt storm can't inflate IOUs.
export const VPN_SESSION_MAX_NANM = BigInt(env('VPN_SESSION_MAX_ANM', '50')) * NANM_PER_ANM;
export const VPN_NODE_DAILY_MAX_NANM = BigInt(env('VPN_NODE_DAILY_MAX_ANM', '500')) * NANM_PER_ANM;
export const VPN_GLOBAL_DAILY_MAX_NANM = BigInt(env('VPN_GLOBAL_DAILY_MAX_ANM', '5000')) * NANM_PER_ANM;
// Upper bound on an exit's self-declared capacity. capacityMbps feeds the per-session capacity
// ceiling, so leaving it unbounded lets an exit claim e.g. 1 Tbps and defeat the ceiling; clamp it.
export const VPN_MAX_CAPACITY_MBPS = Number(env('VPN_MAX_CAPACITY_MBPS', '10000')); // 10 Gbps
export const VPN_ABUSE_THRESHOLD = Number(env('VPN_ABUSE_THRESHOLD', '3')); // OPEN reports => auto-offline
export type ApiScope = (typeof API_SCOPES)[number];

// The only network-accepted signature scheme. 0x1001/0x1002 are forgeable stubs;
// 4098/SPHINCS+ wallets are consensus-stranded. Hard-reject anything else.
export const ML_DSA_65_ALG_ID = 0x1003;
export const SIGN_MESSAGE_DOMAIN = 'animica:signMessage:';
