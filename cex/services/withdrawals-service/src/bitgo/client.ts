/**
 * BitGo API Client
 */

import axios, { type AxiosInstance } from "axios";
import type { Logger } from "pino";
import type { BitGoTransferRequest, BitGoTransferResponse } from "./types.js";

export class BitGoClient {
  private client: AxiosInstance;

  constructor(
    private baseUrl: string,
    private accessToken: string,
    private logger: Logger
  ) {
    this.client = axios.create({
      baseURL: baseUrl,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      timeout: 30000, // 30 seconds
    });

    // Add request/response logging
    this.client.interceptors.request.use((config) => {
      this.logger.debug(
        {
          method: config.method,
          url: config.url,
          data: config.data,
        },
        "BitGo API request"
      );
      return config;
    });

    this.client.interceptors.response.use(
      (response) => {
        this.logger.debug(
          {
            status: response.status,
            url: response.config.url,
          },
          "BitGo API response"
        );
        return response;
      },
      (error) => {
        this.logger.error(
          {
            status: error.response?.status,
            url: error.config?.url,
            error: error.response?.data || error.message,
          },
          "BitGo API error"
        );
        throw error;
      }
    );
  }

  /**
   * Create a transfer (withdrawal)
   */
  async createTransfer(
    walletId: string,
    request: BitGoTransferRequest
  ): Promise<BitGoTransferResponse> {
    const response = await this.client.post<BitGoTransferResponse>(
      `/api/v2/${walletId}/sendcoins`,
      request
    );
    return response.data;
  }

  /**
   * Get transfer status
   */
  async getTransfer(
    walletId: string,
    transferId: string
  ): Promise<BitGoTransferResponse> {
    const response = await this.client.get<BitGoTransferResponse>(
      `/api/v2/${walletId}/transfer/${transferId}`
    );
    return response.data;
  }

  /**
   * Cancel a pending transfer
   */
  async cancelTransfer(walletId: string, transferId: string): Promise<void> {
    await this.client.delete(`/api/v2/${walletId}/transfer/${transferId}`);
  }
}

/**
 * Create BitGo client instance
 */
export function createBitGoClient(
  baseUrl: string,
  accessToken: string,
  logger: Logger
): BitGoClient {
  return new BitGoClient(baseUrl, accessToken, logger);
}
