import axios, { AxiosInstance } from 'axios';
import type {
  Market,
  Orderbook,
  Trade,
  Order,
  Balance,
  UserTrade,
  CreateOrderRequest,
} from '../types';

const API_URL = import.meta.env.VITE_CEX_API_URL || 'http://localhost:3000';

class ApiClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_URL,
      timeout: 10000,
      withCredentials: true,
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
    try {
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
    } catch (error) {
      console.error('Failed to fetch markets:', error);
      // Return mock data as fallback
      return this.getMockMarkets();
    }
  }

  async getOrderbook(symbol: string): Promise<Orderbook> {
    try {
      const { data } = await this.client.get(`/markets/${symbol}/orderbook`);
      return {
        symbol: data.symbol,
        bids: data.bids.map((b: any) => ({
          price: b.price,
          quantity: b.quantity,
          total: b.total,
        })),
        asks: data.asks.map((a: any) => ({
          price: a.price,
          quantity: a.quantity,
          total: a.total,
        })),
        timestamp: data.timestamp,
      };
    } catch (error) {
      console.error('Failed to fetch orderbook:', error);
      return this.getMockOrderbook(symbol);
    }
  }

  async getTrades(symbol: string): Promise<Trade[]> {
    try {
      const { data } = await this.client.get(`/markets/${symbol}/trades`);
      return data.trades.map((t: any) => ({
        id: t.id,
        symbol: t.symbol || symbol,
        price: t.price,
        quantity: t.quantity,
        side: t.side,
        timestamp: t.timestamp,
      }));
    } catch (error) {
      console.error('Failed to fetch trades:', error);
      return this.getMockTrades(symbol);
    }
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
        type: data.type.toLowerCase() as 'limit' | 'market',
        price: data.price,
        quantity: data.quantity,
        filledQuantity: data.filledQuantity || 0,
        status: data.status as any,
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
        type: o.type.toLowerCase() as 'limit' | 'market',
        price: o.price,
        quantity: o.quantity,
        filledQuantity: o.filledQuantity || 0,
        status: o.status,
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
    try {
      const { data } = await this.client.get('/me/balances');
      return data.balances.map((b: any) => ({
        asset: b.asset,
        available: b.available,
        locked: b.locked,
        total: b.total,
      }));
    } catch (error) {
      console.error('Failed to fetch balances:', error);
      return this.getMockBalances();
    }
  }

  // Mock data fallbacks
  private getMockMarkets(): Market[] {
    return [
      {
        symbol: 'ANM-USDT',
        baseAsset: 'ANM',
        quoteAsset: 'USDT',
        lastPrice: 1.25,
        change24h: 5.2,
        volume24h: 1250000,
        high24h: 1.28,
        low24h: 1.18,
      },
      {
        symbol: 'BTC-USDT',
        baseAsset: 'BTC',
        quoteAsset: 'USDT',
        lastPrice: 45000,
        change24h: -2.1,
        volume24h: 25000000,
        high24h: 46000,
        low24h: 44500,
      },
      {
        symbol: 'ETH-USDT',
        baseAsset: 'ETH',
        quoteAsset: 'USDT',
        lastPrice: 2800,
        change24h: 3.5,
        volume24h: 15000000,
        high24h: 2850,
        low24h: 2750,
      },
    ];
  }

  private getMockOrderbook(symbol: string): Orderbook {
    return {
      symbol,
      bids: [
        { price: 1.24, quantity: 100, total: 124 },
        { price: 1.23, quantity: 200, total: 246 },
        { price: 1.22, quantity: 150, total: 183 },
      ],
      asks: [
        { price: 1.25, quantity: 120, total: 150 },
        { price: 1.26, quantity: 180, total: 226.8 },
        { price: 1.27, quantity: 160, total: 203.2 },
      ],
      timestamp: Date.now(),
    };
  }

  private getMockTrades(symbol: string): Trade[] {
    return [
      {
        id: '1',
        symbol,
        price: 1.25,
        quantity: 10,
        side: 'buy',
        timestamp: Date.now() - 1000,
      },
      {
        id: '2',
        symbol,
        price: 1.24,
        quantity: 15,
        side: 'sell',
        timestamp: Date.now() - 2000,
      },
    ];
  }

  private getMockBalances(): Balance[] {
    return [
      { asset: 'USDT', available: 10000, locked: 0, total: 10000 },
      { asset: 'ANM', available: 5000, locked: 100, total: 5100 },
      { asset: 'BTC', available: 0.5, locked: 0, total: 0.5 },
      { asset: 'ETH', available: 2.5, locked: 0.1, total: 2.6 },
    ];
  }
}

export const apiClient = new ApiClient();
