import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "../../utils/classnames";
import { formatNumber, shortHash } from "../../utils/format";
import { RpcClient, JsonValue } from "../../services/rpc";

interface TreasurySnapshot {
  totalSupply: number;
  soldToDate: number;
  treasuryBalance: number;
  percentSold: number;
  revenueToDate: number;
  lastUpdateBlock: number;
  timestamp: string;
  targetRevenue: number;
  yearsToTarget: number | null;
}

interface MarketPriceData {
  price: number;
  marketCap: number;
  volume24h: number;
  change24h: number;
  change7d: number;
  high24h: number;
  low24h: number;
  lastUpdate: string;
  source: string;
}

interface PriceHistoryPoint {
  timestamp: string;
  price: number;
  volume?: number;
}

interface PricingFormula {
  basePrice: number;
  markupPercentage: number;
  treasuryMultiplierFormula: string;
  treasuryTargetRevenue: number;
  deterministic: boolean;
  formula: string;
}

/**
 * Explorer Marketplace & Treasury Page
 * 
 * Displays:
 * - ANM token pricing and market data
 * - Treasury status and progress toward $1B target
 * - Historical price charts
 * - Purchase data (read-only in explorer)
 * - Link to wallet for actual purchases
 */
export function MarketplacePage() {
  const navigate = useNavigate();
  const [rpc, setRpc] = useState<RpcClient | null>(null);
  
  // Data states
  const [treasury, setTreasury] = useState<TreasurySnapshot | null>(null);
  const [marketData, setMarketData] = useState<MarketPriceData | null>(null);
  const [priceHistory, setPriceHistory] = useState<PriceHistoryPoint[]>([]);
  const [pricing, setPricing] = useState<PricingFormula | null>(null);
  
  // UI states
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshInterval, setRefreshInterval] = useState<NodeJS.Timer | null>(null);

  // Initialize RPC and fetch data
  useEffect(() => {
    const initializeRpc = async () => {
      try {
        // Get RPC URL from environment or default
        const rpcUrl = (window as any).ENV?.RPC_URL || "http://localhost:8545";
        const client = new RpcClient({ url: rpcUrl });
        setRpc(client);

        // Fetch all data in parallel
        await Promise.all([
          fetchTreasurySnapshot(client),
          fetchMarketData(client),
          fetchPriceHistory(client),
          fetchPricingFormula(client),
        ]);

        setError(null);
      } catch (err) {
        setError(`Failed to initialize: ${err instanceof Error ? err.message : String(err)}`);
        console.error("RPC initialization error:", err);
      } finally {
        setLoading(false);
      }
    };

    initializeRpc();

    // Set up auto-refresh (every 30 seconds)
    const interval = setInterval(() => {
      if (rpc) {
        refreshData(rpc);
      }
    }, 30000);

    return () => {
      if (interval) clearInterval(interval);
    };
  }, []);

  const fetchTreasurySnapshot = async (client: RpcClient) => {
    try {
      const result = await client.call({
        method: "explorer_getTreasurySnapshot",
        params: [],
      });
      setTreasury(result as any);
    } catch (err) {
      console.error("Failed to fetch treasury snapshot:", err);
      throw err;
    }
  };

  const fetchMarketData = async (client: RpcClient) => {
    try {
      const result = await client.call({
        method: "explorer_getMarketData",
        params: { token: "ANM" },
      });
      setMarketData(result as any);
    } catch (err) {
      console.error("Failed to fetch market data:", err);
      throw err;
    }
  };

  const fetchPriceHistory = async (client: RpcClient) => {
    try {
      const result = await client.call({
        method: "explorer_getPriceHistory",
        params: { token: "ANM", days: 7 },
      });
      const data = result as any;
      const history = data.prices.map((price: number, idx: number) => ({
        timestamp: data.timestamps[idx],
        price,
        volume: data.volumes?.[idx],
      }));
      setPriceHistory(history);
    } catch (err) {
      console.error("Failed to fetch price history:", err);
      throw err;
    }
  };

  const fetchPricingFormula = async (client: RpcClient) => {
    try {
      const result = await client.call({
        method: "marketplace_getPricingCurve",
        params: [],
      });
      setPricing(result as any);
    } catch (err) {
      console.error("Failed to fetch pricing formula:", err);
      throw err;
    }
  };

  const refreshData = async (client: RpcClient) => {
    try {
      await Promise.all([
        fetchTreasurySnapshot(client),
        fetchMarketData(client),
      ]);
    } catch (err) {
      console.error("Auto-refresh failed:", err);
    }
  };

  const navigateToWallet = () => {
    // TODO: Deep link to wallet marketplace page if mobile app
    // For now, open in new tab or show instructions
    window.open("animica://marketplace/buy", "_blank");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-600">Loading marketplace data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <h2 className="text-lg font-semibold text-red-900 mb-2">Error</h2>
          <p className="text-red-700">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const percentSold = treasury?.percentSold ?? 0;
  const yearsToTarget = treasury?.yearsToTarget ?? 0;

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-lg p-6 shadow-lg">
        <h1 className="text-3xl font-bold mb-2">ANM Marketplace</h1>
        <p className="text-blue-100">
          Treasury status, pricing, and token distribution information
        </p>
      </div>

      {/* Price Ticker Card */}
      {marketData && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm hover:shadow-md transition">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">Current Price</h2>
            <span className="text-xs text-gray-500">Updated {new Date(marketData.lastUpdate).toLocaleString()}</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* Price */}
            <div className="bg-blue-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">Price</div>
              <div className="text-2xl font-bold text-blue-600">
                ${marketData.price.toFixed(4)}
              </div>
              <div
                className={cn(
                  "text-sm mt-2 font-semibold",
                  marketData.change24h >= 0 ? "text-green-600" : "text-red-600"
                )}
              >
                {marketData.change24h >= 0 ? "+" : ""}
                {marketData.change24h.toFixed(2)}% (24h)
              </div>
            </div>

            {/* Market Cap */}
            <div className="bg-green-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">Market Cap</div>
              <div className="text-2xl font-bold text-green-600">
                ${formatNumber(marketData.marketCap)}
              </div>
              <div className="text-sm text-gray-600 mt-2">
                {marketData.source}
              </div>
            </div>

            {/* 24h Volume */}
            <div className="bg-purple-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">24h Volume</div>
              <div className="text-2xl font-bold text-purple-600">
                ${formatNumber(marketData.volume24h)}
              </div>
              <div className="text-sm text-gray-600 mt-2">
                7d: {marketData.change7d >= 0 ? "+" : ""}
                {marketData.change7d.toFixed(2)}%
              </div>
            </div>

            {/* Range */}
            <div className="bg-orange-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">24h Range</div>
              <div className="text-sm space-y-1">
                <div className="font-semibold text-orange-600">
                  ${marketData.high24h.toFixed(4)}
                </div>
                <div className="h-1 bg-orange-200 rounded"></div>
                <div className="font-semibold text-orange-600">
                  ${marketData.low24h.toFixed(4)}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Treasury Status */}
      {treasury && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
          <h2 className="text-xl font-semibold mb-4">Treasury Status</h2>

          {/* Revenue Progress */}
          <div className="mb-6 p-4 bg-gradient-to-r from-amber-50 to-orange-50 rounded border border-amber-200">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-sm text-gray-600">Revenue Progress</div>
                <div className="text-2xl font-bold text-amber-700">
                  ${(treasury.revenueToDate / 1_000_000_000).toFixed(3)}B / $1.00B
                </div>
              </div>
              <div className="text-right">
                <div className="text-3xl font-bold text-amber-700">
                  {((treasury.revenueToDate / treasury.targetRevenue) * 100).toFixed(1)}%
                </div>
                <div className="text-xs text-gray-600">
                  {treasury.yearsToTarget !== null
                    ? `${treasury.yearsToTarget.toFixed(1)} years to target`
                    : "Calculating..."}
                </div>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
              <div
                className="bg-gradient-to-r from-amber-500 to-orange-600 h-full transition-all duration-500"
                style={{
                  width: `${Math.min(
                    (treasury.revenueToDate / treasury.targetRevenue) * 100,
                    100
                  )}%`,
                }}
              />
            </div>
          </div>

          {/* Treasury Metrics Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {/* ANM Price */}
            <div className="bg-blue-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">ANM Price</div>
              <div className="text-xl font-bold text-blue-600">
                ${marketData?.price.toFixed(4) ?? "—"}
              </div>
            </div>

            {/* % Sold */}
            <div className="bg-green-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">% Sold</div>
              <div className="text-xl font-bold text-green-600">
                {percentSold.toFixed(2)}%
              </div>
              <div className="text-xs text-gray-600 mt-1">
                {formatNumber(treasury.soldToDate)} ANM
              </div>
            </div>

            {/* Years to Target */}
            <div className="bg-purple-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">Years to Target</div>
              <div className="text-xl font-bold text-purple-600">
                {yearsToTarget.toFixed(1)} yrs
              </div>
            </div>

            {/* Remaining Supply */}
            <div className="bg-orange-50 rounded p-4">
              <div className="text-sm text-gray-600 mb-1">Remaining Supply</div>
              <div className="text-xl font-bold text-orange-600">
                {formatNumber(treasury.treasuryBalance)}
              </div>
              <div className="text-xs text-gray-600 mt-1">
                ANM
              </div>
            </div>
          </div>

          {/* Supply Breakdown */}
          <div className="mt-6 p-4 bg-gray-50 rounded">
            <div className="text-sm font-semibold text-gray-700 mb-3">Supply Distribution</div>
            <div className="space-y-2">
              <div className="flex items-center">
                <div className="w-3 h-3 bg-blue-500 rounded-full mr-3"></div>
                <div className="flex-1">
                  <div className="flex justify-between text-sm">
                    <span>Sold</span>
                    <span className="font-semibold">
                      {percentSold.toFixed(2)}% ({formatNumber(treasury.soldToDate)} ANM)
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2 mt-1">
                    <div
                      className="bg-blue-500 h-full rounded-full"
                      style={{ width: `${percentSold}%` }}
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-center">
                <div className="w-3 h-3 bg-gray-400 rounded-full mr-3"></div>
                <div className="flex-1">
                  <div className="flex justify-between text-sm">
                    <span>Treasury</span>
                    <span className="font-semibold">
                      {(100 - percentSold).toFixed(2)}% ({formatNumber(treasury.treasuryBalance)} ANM)
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2 mt-1">
                    <div
                      className="bg-gray-400 h-full rounded-full"
                      style={{ width: `${100 - percentSold}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Pricing Formula */}
      {pricing && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
          <h2 className="text-xl font-semibold mb-4">Pricing Formula</h2>

          <div className="bg-blue-50 border border-blue-200 rounded p-4 mb-4">
            <div className="font-mono text-sm text-blue-900 break-all">
              {pricing.formula}
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-600 mb-1">Base Price</div>
              <div className="text-lg font-bold text-gray-900">
                ${pricing.basePrice.toFixed(2)}
              </div>
            </div>

            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-600 mb-1">Markup</div>
              <div className="text-lg font-bold text-gray-900">
                {(pricing.markupPercentage * 100).toFixed(0)}%
              </div>
            </div>

            <div className="bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-600 mb-1">Deterministic</div>
              <div className={cn(
                "text-lg font-bold",
                pricing.deterministic ? "text-green-600" : "text-red-600"
              )}>
                {pricing.deterministic ? "Yes" : "No"}
              </div>
            </div>

            <div className="col-span-2 md:col-span-3 bg-gray-50 p-3 rounded">
              <div className="text-xs text-gray-600 mb-1">Treasury Multiplier</div>
              <div className="font-mono text-sm text-gray-900">
                {pricing.treasuryMultiplierFormula}
              </div>
            </div>
          </div>

          {/* Info box */}
          <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded text-sm text-blue-900">
            <strong>How it works:</strong> The final ANM price is deterministically calculated by combining
            market price (with 15% markup), the $1.00 base price, and a treasury multiplier that
            accelerates as tokens are sold. This ensures prices scale with treasury depletion while
            remaining reproducible across all clients.
          </div>
        </div>
      )}

      {/* Price History Chart */}
      {priceHistory.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
          <h2 className="text-xl font-semibold mb-4">Price History (7 Days)</h2>

          <div className="relative h-64 bg-gray-50 rounded p-4 border border-gray-200">
            {/* Simple line chart using SVG */}
            <svg className="w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
              {/* Grid lines */}
              <line x1="0" y1="50" x2="100" y2="50" stroke="#e5e7eb" strokeWidth="0.5" />
              <line x1="0" y1="25" x2="100" y2="25" stroke="#e5e7eb" strokeWidth="0.5" />
              <line x1="0" y1="75" x2="100" y2="75" stroke="#e5e7eb" strokeWidth="0.5" />

              {/* Price line */}
              {priceHistory.length > 1 && (() => {
                const minPrice = Math.min(...priceHistory.map((p) => p.price));
                const maxPrice = Math.max(...priceHistory.map((p) => p.price));
                const range = maxPrice - minPrice || 1;

                const points = priceHistory
                  .map((p, i) => ({
                    x: (i / (priceHistory.length - 1)) * 100,
                    y: 100 - ((p.price - minPrice) / range) * 100,
                  }))
                  .map((p) => `${p.x},${p.y}`)
                  .join(" ");

                return (
                  <>
                    <polyline
                      points={points}
                      fill="none"
                      stroke="#3b82f6"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <polyline
                      points={`0,100 ${points} 100,100`}
                      fill="url(#priceGradient)"
                      opacity="0.3"
                    />
                    <defs>
                      <linearGradient id="priceGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.5" />
                        <stop offset="100%" stopColor="#3b82f6" stopOpacity="0" />
                      </linearGradient>
                    </defs>
                  </>
                );
              })()}
            </svg>
          </div>

          {/* Price range info */}
          <div className="mt-4 grid grid-cols-3 gap-4">
            <div>
              <div className="text-xs text-gray-600">Min</div>
              <div className="text-lg font-bold text-gray-900">
                ${Math.min(...priceHistory.map((p) => p.price)).toFixed(4)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-600">Avg</div>
              <div className="text-lg font-bold text-gray-900">
                $
                {(
                  priceHistory.reduce((sum, p) => sum + p.price, 0) / priceHistory.length
                ).toFixed(4)}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-600">Max</div>
              <div className="text-lg font-bold text-gray-900">
                ${Math.max(...priceHistory.map((p) => p.price)).toFixed(4)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* CTA to Wallet */}
      <div className="bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-lg p-6 shadow-lg">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-xl font-bold mb-1">Ready to buy ANM?</h3>
            <p className="text-blue-100">
              Open the Animica wallet to complete your purchase with various payment methods.
            </p>
          </div>
          <button
            onClick={navigateToWallet}
            className="px-6 py-3 bg-white text-blue-600 font-semibold rounded-lg hover:bg-blue-50 transition whitespace-nowrap ml-4"
          >
            Open Wallet
          </button>
        </div>
      </div>
    </div>
  );
}

export default MarketplacePage;
