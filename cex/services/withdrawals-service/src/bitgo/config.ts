/**
 * BitGo Configuration Loader
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import { createDecipheriv } from "crypto";

const CACHE_TTL_MS = 60_000;
const IV_LENGTH = 12;
const TAG_LENGTH = 16;

export interface BitgoRuntimeConfig {
  baseUrl: string;
  expressUrl?: string;
  accessToken?: string;
  webhookSecret?: string;
  walletPassphrase?: string;
  environment: "test" | "prod";
  wallets: Record<string, string> | null;
  coins: Record<string, any> | null;
  enabled: boolean;
}

function decryptSecret(payload: string, key: Buffer): string {
  const raw = Buffer.from(payload, "base64");
  const iv = raw.subarray(0, IV_LENGTH);
  const tag = raw.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const encrypted = raw.subarray(IV_LENGTH + TAG_LENGTH);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString("utf8");
}

function normalizeKey(key: string): Buffer {
  const normalized = key.trim();
  const buffer =
    normalized.length === 64 ? Buffer.from(normalized, "hex") : Buffer.from(normalized, "base64");
  if (buffer.length !== 32) {
    throw new Error("CONFIG_ENCRYPTION_KEY must be 32 bytes (base64 or hex)");
  }
  return buffer;
}

export class BitgoConfigStore {
  private cached: BitgoRuntimeConfig | null = null;
  private lastLoadedAt = 0;

  constructor(
    private pool: Pool,
    private config: Config,
    private logger: Logger
  ) {}

  async getConfig(): Promise<BitgoRuntimeConfig> {
    const now = Date.now();
    if (this.cached && now - this.lastLoadedAt < CACHE_TTL_MS) {
      return this.cached;
    }

    const result = await this.pool.query("SELECT * FROM bitgo_configs WHERE id = 'default' LIMIT 1");
    const row = result.rows.length > 0 ? result.rows[0] : null;

    if (!row || !row.access_token_encrypted) {
      const fallbackBaseUrl =
        this.config.BITGO_BASE_URL
        ?? (this.config.BITGO_ENV === "prod"
          ? "https://app.bitgo.com"
          : "https://app.bitgo-test.com");

      this.cached = {
        baseUrl: fallbackBaseUrl,
        expressUrl: this.config.BITGO_EXPRESS_URL,
        accessToken: this.config.BITGO_ACCESS_TOKEN,
        webhookSecret: this.config.BITGO_WEBHOOK_SECRET,
        walletPassphrase: this.config.BITGO_WALLET_PASSPHRASE,
        environment: this.config.BITGO_ENV,
        wallets: null,
        coins: null,
        enabled: false,
      };
      this.lastLoadedAt = now;
      return this.cached;
    }

    if (!this.config.CONFIG_ENCRYPTION_KEY) {
      throw new Error("CONFIG_ENCRYPTION_KEY is required to decrypt BitGo settings");
    }

    const key = normalizeKey(this.config.CONFIG_ENCRYPTION_KEY);

    this.cached = {
      baseUrl: row.base_url
        ?? (row.environment === "prod" ? "https://app.bitgo.com" : "https://app.bitgo-test.com"),
      expressUrl: this.config.BITGO_EXPRESS_URL,
      accessToken: decryptSecret(row.access_token_encrypted, key),
      webhookSecret: row.webhook_secret_encrypted
        ? decryptSecret(row.webhook_secret_encrypted, key)
        : undefined,
      walletPassphrase: this.config.BITGO_WALLET_PASSPHRASE,
      environment: row.environment,
      wallets: row.wallets ?? null,
      coins: row.coins ?? null,
      enabled: row.enabled,
    };

    this.lastLoadedAt = now;
    this.logger.info(
      { enabled: this.cached.enabled, environment: this.cached.environment },
      "Loaded BitGo config from database"
    );
    return this.cached;
  }

  invalidate(): void {
    this.lastLoadedAt = 0;
  }
}
