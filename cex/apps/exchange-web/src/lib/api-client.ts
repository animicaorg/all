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

  // Mock data for now - these will need to be implemented in the API Gateway
  async getMarkets(): Promise<Market[]> {
    // Mock data until backend is ready
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

  async getOrderbook(symbol: string): Promise<Orderbook> {
    // Mock data
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

  async getTrades(symbol: string): Promise<Trade[]> {
    // Mock data
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

  async createOrder(order: CreateOrderRequest): Promise<Order> {
    // Mock response
    return {
      id: Math.random().toString(36).substr(2, 9),
      clientOrderId: Math.random().toString(36).substr(2, 9),
      symbol: order.symbol,
      side: order.side,
      type: order.type,
      price: order.price,
      quantity: order.quantity,
      filledQuantity: 0,
      status: 'open',
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
  }

  async cancelOrder(orderId: string): Promise<void> {
    // Mock - would actually call API
    console.log('Cancelling order:', orderId);
  }

  async getMyOrders(_symbol?: string): Promise<Order[]> {
    // Mock data
    return [];
  }

  async getMyTrades(_symbol?: string): Promise<UserTrade[]> {
    // Mock data
    return [];
  }

  async getBalances(): Promise<Balance[]> {
    // Mock data
    return [
      { asset: 'USDT', available: 10000, locked: 0, total: 10000 },
      { asset: 'ANM', available: 5000, locked: 100, total: 5100 },
      { asset: 'BTC', available: 0.5, locked: 0, total: 0.5 },
      { asset: 'ETH', available: 2.5, locked: 0.1, total: 2.6 },
    ];
  }
}

export const apiClient = new ApiClient();
