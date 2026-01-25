/**
 * HTTP Client for E2E Testing
 * 
 * Provides typed HTTP methods for interacting with exchange APIs:
 * - Authentication
 * - Order management
 * - Account queries
 * - Admin operations
 */

export interface HTTPClientOptions {
  baseURL: string;
  apiKey?: string;
  apiSecret?: string;
  timeout?: number;
}

export interface HTTPResponse<T = any> {
  status: number;
  data: T;
  headers: Record<string, string>;
}

export class HTTPClient {
  private baseURL: string;
  private apiKey?: string;
  private apiSecret?: string;
  private timeout: number;
  
  constructor(options: HTTPClientOptions) {
    this.baseURL = options.baseURL;
    this.apiKey = options.apiKey;
    this.apiSecret = options.apiSecret;
    this.timeout = options.timeout || 30000;
  }
  
  /**
   * GET request
   */
  async get<T = any>(path: string, params?: Record<string, any>): Promise<HTTPResponse<T>> {
    const url = this.buildURL(path, params);
    const headers = this.buildHeaders('GET', path);
    
    const response = await fetch(url, {
      method: 'GET',
      headers,
      signal: AbortSignal.timeout(this.timeout),
    });
    
    const data = await response.json();
    
    return {
      status: response.status,
      data,
      headers: Object.fromEntries(response.headers.entries()),
    };
  }
  
  /**
   * POST request
   */
  async post<T = any>(path: string, body?: any): Promise<HTTPResponse<T>> {
    const url = this.buildURL(path);
    const headers = this.buildHeaders('POST', path, body);
    
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(this.timeout),
    });
    
    const data = await response.json();
    
    return {
      status: response.status,
      data,
      headers: Object.fromEntries(response.headers.entries()),
    };
  }
  
  /**
   * DELETE request
   */
  async delete<T = any>(path: string): Promise<HTTPResponse<T>> {
    const url = this.buildURL(path);
    const headers = this.buildHeaders('DELETE', path);
    
    const response = await fetch(url, {
      method: 'DELETE',
      headers,
      signal: AbortSignal.timeout(this.timeout),
    });
    
    const data = await response.json();
    
    return {
      status: response.status,
      data,
      headers: Object.fromEntries(response.headers.entries()),
    };
  }
  
  /**
   * Health check helper
   */
  async health(): Promise<boolean> {
    try {
      const response = await this.get('/health');
      return response.status === 200;
    } catch {
      return false;
    }
  }
  
  /**
   * Build full URL with query params
   */
  private buildURL(path: string, params?: Record<string, any>): string {
    const url = new URL(path, this.baseURL);
    
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.append(key, String(value));
        }
      });
    }
    
    return url.toString();
  }
  
  /**
   * Build request headers with authentication
   */
  private buildHeaders(method: string, path: string, body?: any): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey;
    }
    
    if (this.apiSecret) {
      // Simple HMAC signature (adjust based on actual auth scheme)
      const timestamp = Date.now().toString();
      const payload = `${method}${path}${timestamp}${body ? JSON.stringify(body) : ''}`;
      // TODO: Implement actual signature if needed
      headers['X-Timestamp'] = timestamp;
      headers['X-Signature'] = this.apiSecret; // Placeholder
    }
    
    return headers;
  }
  
  /**
   * Set authentication credentials
   */
  setAuth(apiKey: string, apiSecret?: string): void {
    this.apiKey = apiKey;
    this.apiSecret = apiSecret;
  }
}

/**
 * Exchange API Client
 */
export class ExchangeAPIClient extends HTTPClient {
  /**
   * Place a limit order
   */
  async placeLimitOrder(params: {
    market: string;
    side: 'buy' | 'sell';
    price: string;
    size: string;
    timeInForce?: 'GTC' | 'IOC' | 'FOK';
    clientOrderId?: string;
  }) {
    return this.post('/api/v1/orders', params);
  }
  
  /**
   * Place a market order
   */
  async placeMarketOrder(params: {
    market: string;
    side: 'buy' | 'sell';
    size: string;
    clientOrderId?: string;
  }) {
    return this.post('/api/v1/orders/market', params);
  }
  
  /**
   * Cancel order
   */
  async cancelOrder(orderId: string) {
    return this.delete(`/api/v1/orders/${orderId}`);
  }
  
  /**
   * Get open orders
   */
  async getOpenOrders(market?: string) {
    return this.get('/api/v1/orders', market ? { market } : undefined);
  }
  
  /**
   * Get account balance
   */
  async getBalance() {
    return this.get('/api/v1/account/balance');
  }
  
  /**
   * Get orderbook
   */
  async getOrderbook(market: string) {
    return this.get(`/api/v1/markets/${market}/orderbook`);
  }
  
  /**
   * Get recent trades
   */
  async getTrades(market: string, limit = 100) {
    return this.get(`/api/v1/markets/${market}/trades`, { limit });
  }
}

/**
 * Admin API Client
 */
export class AdminAPIClient extends HTTPClient {
  /**
   * Create test user
   */
  async createUser(params: {
    email: string;
    password: string;
  }) {
    return this.post('/api/admin/users', params);
  }
  
  /**
   * Create API key for user
   */
  async createAPIKey(userId: string) {
    return this.post(`/api/admin/users/${userId}/api-keys`);
  }
  
  /**
   * Create market
   */
  async createMarket(params: {
    baseAsset: string;
    quoteAsset: string;
    minOrderSize: string;
    tickSize: string;
  }) {
    return this.post('/api/admin/markets', params);
  }
  
  /**
   * Get ledger snapshot
   */
  async getLedgerSnapshot() {
    return this.get('/api/admin/ledger/snapshot');
  }
  
  /**
   * Get deposit events
   */
  async getDeposits(params?: {
    userId?: string;
    status?: string;
    limit?: number;
  }) {
    return this.get('/api/admin/deposits', params);
  }
  
  /**
   * Get withdrawal events
   */
  async getWithdrawals(params?: {
    userId?: string;
    status?: string;
    limit?: number;
  }) {
    return this.get('/api/admin/withdrawals', params);
  }
}
