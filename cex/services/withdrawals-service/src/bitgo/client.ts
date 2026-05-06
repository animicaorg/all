/**
 * BitGo API Client
 */

import axios from "axios";
import type { Logger } from "pino";
import type { BitGoTransferRequest, BitGoTransferResponse } from "./types.js";

export interface BitgoConfigProvider {
  getConfig: () => Promise<{
    baseUrl: string;
    expressUrl?: string;
    accessToken?: string;
    walletPassphrase?: string;
  }>;
}

type BitGoEndpoint = "api" | "express";

function isHostedBitGoApiUrl(baseUrl: string): boolean {
  try {
    const hostname = new URL(baseUrl).hostname.toLowerCase();
    return hostname === "app.bitgo.com" || hostname === "app.bitgo-test.com";
  } catch {
    return false;
  }
}

export class BitGoClient {
  constructor(
    private configProvider: BitgoConfigProvider,
    private logger: Logger
  ) {}

  private async request<T>(
    method: "get" | "post" | "delete",
    url: string,
    data?: any,
    endpoint: BitGoEndpoint = "api"
  ) {
    const config = await this.configProvider.getConfig();
    if (!config.accessToken) {
      throw new Error("BitGo access token not configured");
    }

    const baseURL = endpoint === "express" ? config.expressUrl ?? config.baseUrl : config.baseUrl;
    if (endpoint === "express" && !config.expressUrl && isHostedBitGoApiUrl(baseURL)) {
      throw new Error(
        "BITGO_EXPRESS_URL is required for BitGo withdrawals; /sendcoins must be sent to BitGo Express, not the hosted BitGo API"
      );
    }

    const logData =
      data && typeof data === "object" && "walletPassphrase" in data
        ? { ...data, walletPassphrase: "[redacted]" }
        : data;
    this.logger.debug({ method, url, endpoint, data: logData }, "BitGo API request");

    try {
      const response = await axios.request<T>({
        method,
        baseURL,
        url,
        data,
        headers: {
          Authorization: `Bearer ${config.accessToken}`,
          "Content-Type": "application/json",
        },
        timeout: 30000,
      });

      this.logger.debug({ status: response.status, url }, "BitGo API response");
      return response.data;
    } catch (error: any) {
      this.logger.error(
        {
          status: error.response?.status,
          url,
          error: error.response?.data || error.message,
        },
        "BitGo API error"
      );
      throw error;
    }
  }

  /**
   * Create a transfer (withdrawal)
   */
  async createTransfer(
    coin: string,
    walletId: string,
    request: BitGoTransferRequest
  ): Promise<BitGoTransferResponse> {
    const config = await this.configProvider.getConfig();
    const payload =
      config.walletPassphrase && !request.walletPassphrase
        ? { ...request, walletPassphrase: config.walletPassphrase }
        : request;

    return this.request<BitGoTransferResponse>(
      `post`,
      `/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/sendcoins`,
      payload,
      "express"
    );
  }

  /**
   * Get transfer status
   */
  async getTransfer(
    coin: string,
    walletId: string,
    transferId: string
  ): Promise<BitGoTransferResponse> {
    return this.request<BitGoTransferResponse>(
      `get`,
      `/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/transfer/${encodeURIComponent(transferId)}`
    );
  }

  /**
   * Cancel a pending transfer
   */
  async cancelTransfer(coin: string, walletId: string, transferId: string): Promise<void> {
    await this.request(
      `delete`,
      `/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/transfer/${encodeURIComponent(transferId)}`
    );
  }
}

/**
 * Create BitGo client instance
 */
export function createBitGoClient(
  configProvider: BitgoConfigProvider,
  logger: Logger
): BitGoClient {
  return new BitGoClient(configProvider, logger);
}
