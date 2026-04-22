import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios';
import type { Config } from '../../config.js';
import type { Logger } from '../../logger.js';

export class ModernTreasuryClient {
  private readonly http: AxiosInstance;

  constructor(
    private readonly config: Config,
    private readonly logger: Logger
  ) {
    this.http = axios.create({
      baseURL: config.MODERN_TREASURY_BASE_URL,
      timeout: 15_000,
      auth: {
        username: config.MODERN_TREASURY_API_KEY,
        password: ''
      },
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'animica-usdan-api/0.1.0'
      }
    });
  }

  async request<T>(config: AxiosRequestConfig, retryCount = 2): Promise<T> {
    let attempt = 0;
    let latestError: unknown;

    while (attempt <= retryCount) {
      try {
        const response = await this.http.request<T>(config);
        return response.data;
      } catch (error) {
        latestError = error;
        this.logger.warn({ error, attempt }, 'Modern Treasury request failed');
        attempt += 1;
        if (attempt > retryCount) break;
        await new Promise((resolve) => setTimeout(resolve, 200 * attempt));
      }
    }

    throw latestError;
  }
}
