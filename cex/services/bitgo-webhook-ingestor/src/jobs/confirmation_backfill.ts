/**
 * BitGo Deposit Discovery and Confirmation Backfill
 *
 * Webhooks are the fast path, but they are not sufficient for correctness:
 * missed webhook deliveries must not make real deposits invisible. This job
 * polls active BitGo deposit wallets and feeds discovered transfers through
 * the same idempotent ingestion path used by webhooks.
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import { normalizeBitGoWebhook } from "../bitgo/normalize.js";
import type { BitGoWebhookPayload } from "../bitgo/types.js";
import { ingestDeposit } from "../pipeline/ingest.js";
import type { Config } from "../config.js";

interface ActiveBitGoWallet {
  assetNetworkId: string;
  coin: string;
  walletId: string;
}

interface PendingDeposit {
  id: string;
  coin: string;
  walletId: string;
  transferId: string | null;
  txid: string;
}

export class ConfirmationBackfill {
  private running = false;
  private intervalId?: NodeJS.Timeout;
  private iterationInFlight = false;

  constructor(
    private pool: Pool,
    private config: Config,
    private logger: Logger
  ) {}

  start(): void {
    if (this.running) {
      this.logger.warn("Confirmation backfill already running");
      return;
    }

    this.running = true;
    this.logger.info(
      { intervalMs: this.config.CONFIRMATION_BACKFILL_INTERVAL_MS },
      "Starting BitGo deposit discovery job"
    );

    this.run().catch((error) => {
      this.logger.error({ error }, "BitGo deposit discovery initial run failed");
    });

    this.intervalId = setInterval(() => {
      this.run().catch((error) => {
        this.logger.error({ error }, "BitGo deposit discovery iteration failed");
      });
    }, this.config.CONFIRMATION_BACKFILL_INTERVAL_MS);
  }

  stop(): void {
    if (!this.running) {
      return;
    }

    this.running = false;
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = undefined;
    }

    this.logger.info("BitGo deposit discovery stopped");
  }

  private async run(): Promise<void> {
    if (this.iterationInFlight) {
      this.logger.debug("BitGo deposit discovery already running");
      return;
    }

    const token = this.bitgoToken();
    if (!token) {
      this.logger.warn(
        "BitGo API token not configured; webhook-only mode cannot discover missed Litecoin/BTC/DOGE/ZEC deposits"
      );
      return;
    }

    this.iterationInFlight = true;
    try {
      await this.discoverWalletTransfers(token);
      await this.refreshPendingDeposits(token);
    } finally {
      this.iterationInFlight = false;
    }
  }

  private async discoverWalletTransfers(token: string): Promise<void> {
    const wallets = await this.getActiveBitGoWallets();
    if (wallets.length === 0) {
      this.logger.debug("No active BitGo deposit wallets to poll");
      return;
    }

    for (const wallet of wallets) {
      const walletLogger = this.logger.child({
        coin: wallet.coin,
        walletId: wallet.walletId,
        assetNetworkId: wallet.assetNetworkId,
      });

      try {
        const transfers = await this.fetchWalletTransfers(token, wallet.coin, wallet.walletId);
        walletLogger.info(
          { transferCount: transfers.length },
          "Fetched BitGo wallet transfers for deposit discovery"
        );

        for (const transfer of transfers) {
          await this.ingestTransfer(wallet.coin, wallet.walletId, transfer, "wallet_discovery");
        }
      } catch (error) {
        walletLogger.error({ error }, "Failed to discover BitGo wallet transfers");
      }
    }
  }

  private async refreshPendingDeposits(token: string): Promise<void> {
    const deposits = await this.getPendingDeposits();
    if (deposits.length === 0) {
      this.logger.debug("No pending BitGo deposits need confirmation refresh");
      return;
    }

    for (const deposit of deposits) {
      const depositLogger = this.logger.child({
        depositId: deposit.id,
        coin: deposit.coin,
        walletId: deposit.walletId,
        transferId: deposit.transferId,
        txid: deposit.txid,
      });

      if (!deposit.transferId) {
        depositLogger.debug(
          "Pending BitGo deposit has no transfer id; wallet discovery will refresh it when present in the recent transfer list"
        );
        continue;
      }

      try {
        const transfer = await this.fetchTransfer(
          token,
          deposit.coin,
          deposit.walletId,
          deposit.transferId
        );
        await this.ingestTransfer(deposit.coin, deposit.walletId, transfer, "confirmation_refresh");
      } catch (error) {
        depositLogger.error({ error }, "Failed to refresh BitGo deposit confirmation state");
      }
    }
  }

  private async ingestTransfer(
    coin: string,
    walletId: string,
    transfer: any,
    source: "wallet_discovery" | "confirmation_refresh"
  ): Promise<void> {
    if (!transfer?.id || !transfer?.txid) {
      this.logger.debug(
        {
          coin,
          walletId,
          transferId: transfer?.id,
          hasTxid: Boolean(transfer?.txid),
          source,
        },
        "Skipping BitGo transfer without the fields required for deposit ingestion"
      );
      return;
    }

    const payload: BitGoWebhookPayload = {
      type: "transfer",
      walletId,
      coin,
      transfer,
    };

    const client = await this.pool.connect();
    let observations;
    try {
      observations = await normalizeBitGoWebhook(payload, client, this.logger);
    } finally {
      client.release();
    }

    for (const observation of observations) {
      try {
        const result = await ingestDeposit(this.pool, observation, this.logger);
        this.logger.info(
          {
            depositId: result.depositId,
            status: result.status,
            isNew: result.isNew,
            userId: result.userId,
            unassigned: result.unassigned,
            txid: observation.txid,
            address: observation.address,
            amountAtoms: observation.amountAtoms.toString(),
            source,
          },
          result.isNew ? "BitGo deposit discovered" : "BitGo deposit refreshed"
        );
      } catch (error) {
        this.logger.error(
          {
            error,
            txid: observation.txid,
            address: observation.address,
            source,
          },
          "Failed to ingest BitGo transfer observation"
        );
      }
    }
  }

  private async getActiveBitGoWallets(): Promise<ActiveBitGoWallet[]> {
    const result = await this.pool.query(
      `SELECT DISTINCT
         asset_networks.id::text AS asset_network_id,
         asset_networks.bitgo_coin AS coin,
         wallets.wallet_id AS wallet_id
       FROM wallets
       JOIN asset_networks ON asset_networks.id = wallets.asset_network_id
       JOIN assets ON assets.id = asset_networks.asset_id
       JOIN networks ON networks.id = asset_networks.network_id
       WHERE wallets.provider = 'BITGO'
         AND wallets.status = 'ACTIVE'
         AND asset_networks.deposits_enabled = true
         AND assets.active = true
         AND networks.active = true
         AND asset_networks.bitgo_coin IS NOT NULL
       ORDER BY asset_networks.bitgo_coin, wallets.wallet_id`
    );

    return result.rows.map((row) => ({
      assetNetworkId: row.asset_network_id,
      coin: row.coin,
      walletId: row.wallet_id,
    }));
  }

  private async getPendingDeposits(): Promise<PendingDeposit[]> {
    const result = await this.pool.query(
      `SELECT
         deposits.id::text AS id,
         asset_networks.bitgo_coin AS coin,
         deposits.wallet_id AS wallet_id,
         deposits.transfer_id,
         deposits.txid
       FROM deposits
       JOIN asset_networks ON asset_networks.id = deposits.asset_network_id
       WHERE deposits.provider = 'BITGO'
         AND deposits.status = 'DETECTED'
         AND deposits.created_at < NOW() - INTERVAL '1 minute'
         AND asset_networks.bitgo_coin IS NOT NULL
       ORDER BY deposits.created_at ASC
       LIMIT 50`
    );

    return result.rows.map((row) => ({
      id: row.id,
      coin: row.coin,
      walletId: row.wallet_id,
      transferId: row.transfer_id,
      txid: row.txid,
    }));
  }

  private async fetchWalletTransfers(
    token: string,
    coin: string,
    walletId: string
  ): Promise<any[]> {
    const url = new URL(
      `${this.bitgoBaseUrl()}/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/transfer`
    );
    url.searchParams.set("limit", String(this.config.BITGO_TRANSFER_DISCOVERY_LIMIT));

    const body = await this.fetchBitGoJson(token, url);
    if (Array.isArray(body.transfers)) {
      return body.transfers;
    }
    if (Array.isArray(body.transfer)) {
      return body.transfer;
    }
    if (body.transfer) {
      return [body.transfer];
    }
    return [];
  }

  private async fetchTransfer(
    token: string,
    coin: string,
    walletId: string,
    transferId: string
  ): Promise<any> {
    const url = new URL(
      `${this.bitgoBaseUrl()}/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/transfer/${encodeURIComponent(transferId)}`
    );
    const body = await this.fetchBitGoJson(token, url);
    return body.transfer || body;
  }

  private async fetchBitGoJson(token: string, url: URL): Promise<any> {
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(
        `BitGo API error ${response.status} ${response.statusText}: ${text.slice(0, 300)}`
      );
    }

    return response.json();
  }

  private bitgoToken(): string | undefined {
    return this.config.BITGO_API_TOKEN || this.config.BITGO_ACCESS_TOKEN;
  }

  private bitgoBaseUrl(): string {
    const configured = this.config.BITGO_BASE_URL || this.config.BITGO_API_URL;
    const baseUrl =
      configured ||
      (this.config.BITGO_ENV === "prod"
        ? "https://app.bitgo.com"
        : "https://app.bitgo-test.com");
    return baseUrl.replace(/\/+$/, "");
  }
}
