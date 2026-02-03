/**
 * BitGo API Client
 */

import axios from "axios";
import type { Logger } from "pino";
import type { BitGoTransferRequest, BitGoTransferResponse } from "./types.js";

export interface BitgoConfigProvider {
  getConfig: () => Promise<{
    baseUrl: string;
    accessToken?: string;
  }>;
}

export class BitGoClient {
  constructor(
    private configProvider: BitgoConfigProvider,
    private logger: Logger
  ) {}

  private async request<T>(method: "get" | "post" | "delete", url: string, data?: any) {
    const config = await this.configProvider.getConfig();
    if (!config.accessToken) {
      throw new Error("BitGo access token not configured");
    }

    this.logger.debug({ method, url, data }, "BitGo API request");

    try {
      const response = await axios.request<T>({
        method,
        baseURL: config.baseUrl,
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
    walletId: string,
    request: BitGoTransferRequest
  ): Promise<BitGoTransferResponse> {
    return this.request<BitGoTransferResponse>(`post`, `/api/v2/${walletId}/sendcoins`, request);
  }

  /**
   * Get transfer status
   */
  async getTransfer(
    walletId: string,
    transferId: string
  ): Promise<BitGoTransferResponse> {
    return this.request<BitGoTransferResponse>(`get`, `/api/v2/${walletId}/transfer/${transferId}`);
  }

  /**
   * Cancel a pending transfer
   */
  async cancelTransfer(walletId: string, transferId: string): Promise<void> {
    await this.request(`delete`, `/api/v2/${walletId}/transfer/${transferId}`);
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
