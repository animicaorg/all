/**
 * Runtime configuration for the academy API. All inputs come from
 * environment variables so we can swap dev / staging / prod without
 * rebuilding the image.
 *
 *   PORT                       — listen port (default 4324)
 *   ACADEMY_DB_PATH            — SQLite file (default ./academy.db)
 *   ACADEMY_NODE_RPC           — chain RPC for payout submission
 *   ACADEMY_REWARD_POOL_ADDR   — address holding the reward pool
 *   ACADEMY_PAYOUT_SIGNER_KEY  — encrypted keystore path (server signs payouts
 *                                from the pool address; the wallet for the
 *                                pool must live on the server, not the user)
 *   ACADEMY_PAYOUTS_ENABLED    — "1" to allow on-chain payouts; otherwise
 *                                claims succeed with txHash=null (useful in
 *                                staging where the pool address is unfunded)
 *   ACADEMY_CORS_ORIGINS       — comma-separated allowed Origins
 *   ACADEMY_NONCE_TTL_SECONDS  — nonce expiry (default 600)
 */
export interface AcademyConfig {
  port: number;
  dbPath: string;
  nodeRpc: string;
  rewardPoolAddress: string;
  payoutSignerKeystore: string | null;
  payoutsEnabled: boolean;
  corsOrigins: string[];
  nonceTtlSeconds: number;
}

export function loadConfig(): AcademyConfig {
  return {
    port: Number(process.env.PORT ?? 4324),
    dbPath: process.env.ACADEMY_DB_PATH ?? "./academy.db",
    nodeRpc: process.env.ACADEMY_NODE_RPC ?? "http://127.0.0.1:8545",
    rewardPoolAddress:
      process.env.ACADEMY_REWARD_POOL_ADDR ??
      "anim1academy0reward0pool0000000000000000",
    payoutSignerKeystore: process.env.ACADEMY_PAYOUT_SIGNER_KEY ?? null,
    payoutsEnabled: process.env.ACADEMY_PAYOUTS_ENABLED === "1",
    corsOrigins: (process.env.ACADEMY_CORS_ORIGINS ?? "https://academy.animica.org")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    nonceTtlSeconds: Number(process.env.ACADEMY_NONCE_TTL_SECONDS ?? 600),
  };
}
