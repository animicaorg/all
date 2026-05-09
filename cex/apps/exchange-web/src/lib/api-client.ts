import axios, { AxiosHeaders, AxiosInstance } from 'axios';
import type {
  Market,
  Orderbook,
  Trade,
  Order,
  Balance,
  Asset,
  Candle,
  DepositAddress,
  Deposit,
  UserTrade,
  CreateOrderRequest,
  CreateWithdrawalRequest,
  Withdrawal,
  PlatformStats,
  UsdQuote,
  AirdropStatus,
  AirdropTransfer,
  ApiKeySummary,
  CreatedApiKey,
  TradingBot,
  StartTradingBotRequest,
} from '../types';


import { getApiBaseUrl } from './endpoints';

const API_URL = getApiBaseUrl();

function mapOrderStatus(status: string): Order['status'] {
  switch (String(status).toUpperCase()) {
    case 'ACCEPTED':
    case 'PARTIAL_FILL':
    case 'OPEN':
      return 'open';
    case 'FILLED':
      return 'filled';
    case 'CANCELED':
    case 'CANCELLED':
      return 'cancelled';
    case 'REJECTED':
      return 'rejected';
    case 'EXPIRED':
      return 'expired';
    case 'PENDING':
      return 'pending';
    default:
      return 'pending';
  }
}

class ApiClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_URL,
      timeout: 10000,
      withCredentials: true,
    });

    this.client.interceptors.request.use((config) => {
      try {
        const raw =
          typeof window !== 'undefined'
            ? window.localStorage.getItem('auth-storage')
            : null;

        if (raw) {
          const parsed = JSON.parse(raw);
          const state = parsed?.state ?? parsed;
          const userId =
            state?.user?.id ??
            state?.userId ??
            state?.user?.userId ??
            null;
          if (userId) {
            const headers = AxiosHeaders.from(config.headers);
            headers.set('x-user-id', String(userId));
            config.headers = headers;
          }
        }
      } catch {
        // ignore auth-storage parse errors
      }
      return config;
    });
  }

  // Health check
  async health() {
    const { data } = await this.client.get('/healthz');
    return data;
  }

  // Meta / Capabilities
  async getMeta() {
    const { data } = await this.client.get('/meta');
    return data;
  }

  // Markets
  async getMarkets(): Promise<Market[]> {
    const { data } = await this.client.get('/markets');
    return data.markets.map((m: any) => ({
      symbol: m.symbol,
      baseAsset: m.baseAsset,
      quoteAsset: m.quoteAsset,
      lastPrice: m.lastPrice,
      change24h: m.priceChange24h,
      volume24h: m.volume24h,
      high24h: m.high24h,
      low24h: m.low24h,
      priceTick: m.priceTick,
      sizeStep: m.sizeStep,
      minOrderSize: m.minOrderSize,
      makerFeeBps: m.makerFeeBps,
      takerFeeBps: m.takerFeeBps,
    }));
  }

  async getOrderbook(symbol: string): Promise<Orderbook> {
    const { data } = await this.client.get(`/markets/${symbol}/orderbook`);
    return {
      symbol: data.symbol,
      bids: data.bids.map((b: any) => ({
        price: b.price,
        quantity: b.quantity,
        total: Number(b.total ?? 0),
      })),
      asks: data.asks.map((a: any) => ({
        price: a.price,
        quantity: a.quantity,
        total: Number(a.total ?? 0),
      })),
      timestamp: data.timestamp,
    };
  }

  async getTrades(symbol: string): Promise<Trade[]> {
    const { data } = await this.client.get(`/markets/${symbol}/trades`);
    return data.trades.map((t: any) => ({
      id: t.id,
      symbol: t.symbol || symbol,
      price: t.price,
      quantity: t.quantity,
      side: t.side,
      timestamp: t.timestamp,
    }));
  }

  async getCandles(symbol: string, resolution = '1m', limit = 300): Promise<Candle[]> {
    const { data } = await this.client.get(`/markets/${symbol}/candles`, {
      params: { resolution, limit },
    });
    return data.candles.map((candle: any) => ({
      timestamp: candle.timestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
    }));
  }

  async createOrder(order: CreateOrderRequest): Promise<Order> {
    try {
      const { data } = await this.client.post('/orders', {
        symbol: order.symbol,
        side: order.side,
        type: order.type.toUpperCase(),
        price: order.price,
        quantity: order.quantity,
        clientOrderId: order.clientOrderId,
        idempotencyKey: order.idempotencyKey,
      });

      return {
        id: data.orderId,
        clientOrderId: data.clientOrderId,
        symbol: data.symbol,
        side: data.side,
        type: data.type.toLowerCase() as 'limit' | 'market' | 'post_only',
        price: data.price,
        quantity: data.quantity,
        filledQuantity: data.filledQuantity || 0,
        status: mapOrderStatus(data.status),
        createdAt: Date.now(),
        updatedAt: Date.now(),
      };
    } catch (error: any) {
      console.error('Failed to create order:', error);
      throw new Error(error.response?.data?.error || 'Failed to create order');
    }
  }

  async cancelOrder(orderId: string): Promise<void> {
    try {
      await this.client.delete(`/orders/${orderId}`);
    } catch (error: any) {
      console.error('Failed to cancel order:', error);
      throw new Error(error.response?.data?.error || 'Failed to cancel order');
    }
  }

  async getMyOrders(symbol?: string): Promise<Order[]> {
    try {
      const params = symbol ? { symbol } : {};
      const { data } = await this.client.get('/me/orders', { params });
      return data.orders.map((o: any) => ({
        id: o.id,
        clientOrderId: o.clientOrderId,
        symbol: o.symbol,
        side: o.side,
        type: o.type.toLowerCase() as 'limit' | 'market' | 'post_only',
        price: o.price,
        quantity: o.quantity,
        filledQuantity: o.filledQuantity || 0,
        status: mapOrderStatus(o.status),
        createdAt: o.createdAt,
        updatedAt: o.acceptedAt || o.createdAt,
      }));
    } catch (error) {
      console.error('Failed to fetch orders:', error);
      return [];
    }
  }

  async getMyTrades(symbol?: string): Promise<UserTrade[]> {
    try {
      const params = symbol ? { symbol } : {};
      const { data } = await this.client.get('/me/trades', { params });
      return data.trades.map((t: any) => ({
        id: t.id,
        orderId: t.orderId,
        symbol: t.symbol,
        side: t.side,
        price: t.price,
        quantity: t.quantity,
        fee: t.fee,
        feeAsset: t.feeAsset,
        timestamp: t.timestamp,
      }));
    } catch (error) {
      console.error('Failed to fetch trades:', error);
      return [];
    }
  }

  async getBalances(): Promise<Balance[]> {
    const { data } = await this.client.get('/me/balances');
    return data.balances.map((b: any) => ({
      asset: b.asset,
      available: Number(b.available ?? 0),
      locked: Number(b.locked ?? 0),
      total: Number(b.total ?? 0),
    }));
  }

  async getAssets(): Promise<Asset[]> {
    const { data } = await this.client.get('/assets');
    return data.assets;
  }

  async getUsdQuotes(assets: string[]): Promise<UsdQuote[]> {
    const { data } = await this.client.get('/prices/usd', {
      params: { assets: assets.join(',') },
    });
    return data.quotes ?? [];
  }

  async getAirdropStatus(): Promise<AirdropStatus> {
    const { data } = await this.client.get('/airdrop');
    return data;
  }

  async claimAirdrop(): Promise<AirdropTransfer> {
    const { data } = await this.client.post('/airdrop/claim');
    return data;
  }

  async depositAirdrop(amountAtoms: string): Promise<AirdropTransfer> {
    const { data } = await this.client.post('/airdrop/deposit', { amountAtoms });
    return data;
  }

  async getApiKeys(): Promise<ApiKeySummary[]> {
    const { data } = await this.client.get('/me/api-keys');
    return data.apiKeys ?? [];
  }

  async createApiKey(name: string, scopes: string[]): Promise<CreatedApiKey> {
    const { data } = await this.client.post('/me/api-keys', { name, scopes });
    return data;
  }

  async revokeApiKey(id: string): Promise<void> {
    await this.client.delete(`/me/api-keys/${id}`);
  }

  async getTradingBots(): Promise<TradingBot[]> {
    const { data } = await this.client.get('/me/trading-bots');
    return data.bots ?? [];
  }

  async startTradingBot(request: StartTradingBotRequest): Promise<TradingBot> {
    const { data } = await this.client.post('/me/trading-bots/start', request);
    return data.bot;
  }

  async stopTradingBot(id: string): Promise<TradingBot> {
    const { data } = await this.client.post(`/me/trading-bots/${id}/stop`);
    return data.bot;
  }

  async getDepositAddresses(assetNetworkId?: string): Promise<DepositAddress[]> {
    const { data } = await this.client.get('/me/deposit-addresses', {
      params: assetNetworkId ? { assetNetworkId } : {},
    });
    return data.depositAddresses;
  }

  async createDepositAddress(assetNetworkId: string): Promise<DepositAddress> {
    const { data } = await this.client.post('/me/deposit-addresses', { assetNetworkId });
    return data.depositAddress;
  }

  async getDeposits(): Promise<Deposit[]> {
    const { data } = await this.client.get('/deposits');
    return data.deposits;
  }

  async createWithdrawal(request: CreateWithdrawalRequest): Promise<Withdrawal> {
    const idempotencyKey =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? `withdrawal-${crypto.randomUUID()}`
        : `withdrawal-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    const { data } = await this.client.post('/withdrawals', request, {
      headers: { 'Idempotency-Key': idempotencyKey },
    });
    return data;
  }

  async getWithdrawals(): Promise<Withdrawal[]> {
    const { data } = await this.client.get('/withdrawals');
    return data.withdrawals;
  }

  // Platform Statistics
  async getStats(): Promise<PlatformStats> {
    const { data } = await this.client.get('/stats');
    const volume24h = typeof data?.volume24h === 'number' && Number.isFinite(data.volume24h) ? data.volume24h : 0;
    const activeTraders = typeof data?.activeTraders === 'number' && Number.isFinite(data.activeTraders) ? data.activeTraders : 0;
    const uptimePercentage =
      typeof data?.uptimePercentage === 'number' && Number.isFinite(data.uptimePercentage)
        ? data.uptimePercentage
        : null;

    return {
      volume24h,
      activeTraders,
      uptimePercentage,
    };
  }

}

export const apiClient = new ApiClient();
