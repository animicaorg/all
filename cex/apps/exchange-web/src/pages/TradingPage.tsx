import { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, X, Activity } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { apiClient } from '../lib/api-client';
import { useWSStore } from '../lib/ws-store';
import { OrderEntry } from '../components/OrderEntry';
import { MarketChart } from '../components/MarketChart';
import type { CreateOrderRequest, Market } from '../types';

export default function TradingPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  const { subscribe, unsubscribe, orderbooks, trades, tickers, connectionState, getStats } = useWSStore();

  // Get market info
  const { data: markets = [] } = useQuery({
    queryKey: ['markets'],
    queryFn: () => apiClient.getMarkets(),
  });

  const market = useMemo(() => {
    return markets.find((m) => m.symbol === symbol);
  }, [markets, symbol]);

  // Subscribe to WebSocket channels
  useEffect(() => {
    if (!symbol) return;

    subscribe('orderbook', symbol);
    subscribe('trades', symbol);
    subscribe('ticker', symbol);

    return () => {
      unsubscribe('orderbook', symbol);
      unsubscribe('trades', symbol);
      unsubscribe('ticker', symbol);
    };
  }, [symbol, subscribe, unsubscribe]);

  // Get data from WebSocket store
  const orderbook = orderbooks.get(symbol || '');
  const marketTrades = trades.get(symbol || '') || [];
  const ticker = tickers.get(symbol || '');

  // Fallback to REST if WebSocket not available
  const { data: restOrderbook } = useQuery({
    queryKey: ['orderbook', symbol],
    queryFn: () => apiClient.getOrderbook(symbol!),
    enabled: !!symbol && !orderbook,
    refetchInterval: 2000,
  });

  const { data: restTrades = [] } = useQuery({
    queryKey: ['trades', symbol],
    queryFn: () => apiClient.getTrades(symbol!),
    enabled: !!symbol && marketTrades.length === 0,
    refetchInterval: 2000,
  });

  const { data: myOrders = [] } = useQuery({
    queryKey: ['myOrders', symbol],
    queryFn: () => apiClient.getMyOrders(symbol),
    refetchInterval: 5000,
  });

  const { data: balances = [] } = useQuery({
    queryKey: ['balances'],
    queryFn: () => apiClient.getBalances(),
  });

  const createOrderMutation = useMutation({
    mutationFn: (order: CreateOrderRequest) => apiClient.createOrder(order),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['myOrders'] });
      queryClient.invalidateQueries({ queryKey: ['balances'] });
    },
  });

  const cancelOrderMutation = useMutation({
    mutationFn: (orderId: string) => apiClient.cancelOrder(orderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['myOrders'] });
      queryClient.invalidateQueries({ queryKey: ['balances'] });
      toast.success('Order cancelled');
    },
    onError: (error: any) => {
      toast.error(error.message || 'Failed to cancel order');
    },
  });

  if (!symbol || !market) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <h2 className="text-2xl font-bold text-white mb-2">Market not found</h2>
          <button
            onClick={() => navigate('/markets')}
            className="text-blue-400 hover:text-blue-300"
          >
            Back to Markets
          </button>
        </div>
      </div>
    );
  }

  const displayOrderbook = orderbook || restOrderbook;
  const displayTrades = marketTrades.length > 0 ? marketTrades : restTrades;

  const [baseAsset, quoteAsset] = symbol.split('-');
  const baseBalance = balances.find((b) => b.asset === baseAsset);
  const quoteBalance = balances.find((b) => b.asset === quoteAsset);

  const lastPrice = ticker?.lastPrice || market.lastPrice;
  const priceChange = ticker?.priceChange24h || market.change24h;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/markets')}
            className="p-2 hover:bg-slate-700 rounded-lg"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-3xl font-bold text-white">{symbol}</h1>
            <p className="text-slate-400">{baseAsset} / {quoteAsset}</p>
          </div>
          <div className="flex items-center gap-4 ml-8">
            <div>
              <p className="text-sm text-slate-400">Last Price</p>
              <p className={`text-2xl font-bold ${priceChange >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {lastPrice.toFixed(market.priceTick ? Math.log10(1 / market.priceTick) : 2)}
              </p>
            </div>
            <div>
              <p className="text-sm text-slate-400">24h Change</p>
              <p className={`text-lg font-semibold ${priceChange >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
              </p>
            </div>
          </div>
        </div>
        
        <button
          onClick={() => setShowDiagnostics(!showDiagnostics)}
          className="p-2 hover:bg-slate-700 rounded-lg"
          title="Diagnostics"
        >
          <Activity size={20} />
        </button>
      </div>

      {/* Diagnostics Drawer */}
      {showDiagnostics && (
        <div className="bg-slate-800 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-white mb-3">Diagnostics</h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-slate-400">Connection State</p>
              <p className="text-white font-mono">{connectionState}</p>
            </div>
            <div>
              <p className="text-slate-400">Subscriptions</p>
              <p className="text-white font-mono">{getStats()?.subscriptions.length || 0}</p>
            </div>
            <div>
              <p className="text-slate-400">Orderbook Sequence</p>
              <p className="text-white font-mono">{orderbook?.sequence || 'N/A'}</p>
            </div>
            <div>
              <p className="text-slate-400">Latency</p>
              <p className="text-white font-mono">
                {getStats()?.latency ? `${getStats()?.latency}ms` : 'N/A'}
              </p>
            </div>
          </div>
        </div>
      )}

      <MarketChart symbol={symbol} market={market} trades={displayTrades} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Orderbook */}
        <div className="bg-slate-800 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-white mb-4">Order Book</h2>
          
          {!displayOrderbook ? (
            <div className="text-center py-8 text-slate-400">
              <p>Loading orderbook...</p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Asks (reversed to show from low to high) */}
              <div>
                <div className="text-xs text-slate-400 mb-2 grid grid-cols-3 gap-2">
                  <span>Price</span>
                  <span className="text-right">Amount</span>
                  <span className="text-right">Total</span>
                </div>
                <div className="space-y-1">
                  {displayOrderbook.asks.slice(0, 10).reverse().map((ask, i) => (
                    <div key={i} className="text-sm grid grid-cols-3 gap-2 text-red-400 hover:bg-slate-700 p-1 rounded cursor-pointer">
                      <span>{ask.price.toFixed(market.priceTick ? Math.log10(1 / market.priceTick) : 2)}</span>
                      <span className="text-right">{ask.quantity.toFixed(4)}</span>
                      <span className="text-right text-slate-400">{(ask.price * ask.quantity).toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Spread */}
              <div className="text-center py-2 bg-slate-700 rounded">
                <span className="text-sm text-slate-400">Spread: </span>
                <span className="text-sm text-white font-medium">
                  {displayOrderbook.asks[0] && displayOrderbook.bids[0]
                    ? (displayOrderbook.asks[0].price - displayOrderbook.bids[0].price).toFixed(market.priceTick ? Math.log10(1 / market.priceTick) : 2)
                    : '-'}
                </span>
              </div>

              {/* Bids */}
              <div>
                <div className="space-y-1">
                  {displayOrderbook.bids.slice(0, 10).map((bid, i) => (
                    <div key={i} className="text-sm grid grid-cols-3 gap-2 text-green-400 hover:bg-slate-700 p-1 rounded cursor-pointer">
                      <span>{bid.price.toFixed(market.priceTick ? Math.log10(1 / market.priceTick) : 2)}</span>
                      <span className="text-right">{bid.quantity.toFixed(4)}</span>
                      <span className="text-right text-slate-400">{(bid.price * bid.quantity).toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Center: Recent Trades and Orders */}
        <div className="space-y-6">
          {/* Recent Trades */}
          <div className="bg-slate-800 rounded-lg p-4">
            <h2 className="text-lg font-semibold text-white mb-4">Recent Trades</h2>
            {displayTrades.length === 0 ? (
              <div className="text-center py-8 text-slate-400">
                <p>No recent trades</p>
              </div>
            ) : (
              <div className="space-y-2">
                {displayTrades.slice(0, 20).map((trade) => (
                  <div key={trade.id} className="flex justify-between text-sm">
                    <span className={trade.side === 'buy' ? 'text-green-400' : 'text-red-400'}>
                      {trade.price.toFixed(market.priceTick ? Math.log10(1 / market.priceTick) : 2)}
                    </span>
                    <span className="text-slate-300">{trade.quantity.toFixed(4)}</span>
                    <span className="text-slate-400">
                      {new Date(trade.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* My Open Orders */}
          <div className="bg-slate-800 rounded-lg p-4">
            <h2 className="text-lg font-semibold text-white mb-4">My Open Orders</h2>
            {myOrders.length === 0 ? (
              <p className="text-slate-400 text-sm text-center py-4">No open orders</p>
            ) : (
              <div className="space-y-2">
                {myOrders.map((order) => (
                  <div
                    key={order.id}
                    className="flex items-center justify-between text-sm border-b border-slate-700 pb-2"
                  >
                    <div className="flex-1">
                      <span className={order.side === 'buy' ? 'text-green-400' : 'text-red-400'}>
                        {order.side.toUpperCase()}
                      </span>
                      {' '}
                      <span className="text-slate-300">{order.type}</span>
                    </div>
                    <div className="flex-1 text-slate-300 text-center">
                      {order.price?.toFixed(2)} × {order.quantity.toFixed(4)}
                    </div>
                    <button
                      onClick={() => cancelOrderMutation.mutate(order.id)}
                      disabled={cancelOrderMutation.isPending}
                      className="text-red-400 hover:text-red-300 disabled:opacity-50"
                    >
                      <X size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Order Entry */}
        <OrderEntry
          market={market}
          balances={balances}
          onSubmit={(order) => createOrderMutation.mutateAsync(order)}
          isSubmitting={createOrderMutation.isPending}
        />
      </div>
    </div>
  );
}
