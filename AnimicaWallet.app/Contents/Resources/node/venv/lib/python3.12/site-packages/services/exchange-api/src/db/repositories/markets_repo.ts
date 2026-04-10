/**
 * Markets Repository
 * Data access layer for markets
 */

import type { PrismaClient, Market, MarketStatus } from '@prisma/client';

export class MarketsRepository {
  constructor(private prisma: PrismaClient) {}

  /**
   * Get all active markets
   */
  async getActiveMarkets(): Promise<Market[]> {
    return this.prisma.market.findMany({
      where: {
        status: 'ONLINE' as MarketStatus,
      },
      include: {
        baseAsset: true,
        quoteAsset: true,
      },
      orderBy: {
        symbol: 'asc',
      },
    });
  }

  /**
   * Get market by symbol (e.g., "ANM-USD")
   */
  async getMarketBySymbol(symbol: string): Promise<Market | null> {
    return this.prisma.market.findUnique({
      where: { symbol },
      include: {
        baseAsset: true,
        quoteAsset: true,
      },
    });
  }

  /**
   * Get market by ID
   */
  async getMarketById(id: string): Promise<Market | null> {
    return this.prisma.market.findUnique({
      where: { id },
      include: {
        baseAsset: true,
        quoteAsset: true,
      },
    });
  }

  /**
   * Get multiple markets by symbols
   */
  async getMarketsBySymbols(symbols: string[]): Promise<Market[]> {
    return this.prisma.market.findMany({
      where: {
        symbol: { in: symbols },
      },
      include: {
        baseAsset: true,
        quoteAsset: true,
      },
    });
  }

  /**
   * Check if market exists and is online
   */
  async isMarketOnline(symbol: string): Promise<boolean> {
    const market = await this.prisma.market.findUnique({
      where: { symbol },
      select: { status: true },
    });
    return market?.status === 'ONLINE';
  }
}
