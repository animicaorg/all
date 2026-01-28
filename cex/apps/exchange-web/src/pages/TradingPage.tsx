import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, X } from 'lucide-react';
import { apiClient } from '../lib/api-client';
import type { CreateOrderRequest } from '../types';

export default function TradingPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [orderSide, setOrderSide] = useState<'buy' | 'sell'>('buy');
  const [orderType, setOrderType] = useState<'limit' | 'market'>('limit');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');

  const { data: orderbook } = useQuery({
    queryKey: ['orderbook', symbol],
    queryFn: () => apiClient.getOrderbook(symbol!),
    refetchInterval: 1000,
    enabled: !!symbol,
  });

  const { data: trades = [] } = useQuery({
    queryKey: ['trades', symbol],
    queryFn: () => apiClient.getTrades(symbol!),
    refetchInterval: 1000,
    enabled: !!symbol,
  });

  const { data: myOrders = [] } = useQuery({
    queryKey: ['myOrders', symbol],
    queryFn: () => apiClient.getMyOrders(symbol),
    refetchInterval: 2000,
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
      setPrice('');
      setQuantity('');
    },
  });

  const cancelOrderMutation = useMutation({
    mutationFn: (orderId: string) => apiClient.cancelOrder(orderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['myOrders'] });
      queryClient.invalidateQueries({ queryKey: ['balances'] });
    },
  });

  const handleSubmitOrder = () => {
    if (!symbol) return;
    
    const order: CreateOrderRequest = {
      symbol,
      side: orderSide,
      type: orderType,
      quantity: parseFloat(quantity),
    };

    if (orderType === 'limit') {
      order.price = parseFloat(price);
    }

    createOrderMutation.mutate(order);
  };

  const total = orderType === 'limit' && price && quantity
    ? (parseFloat(price) * parseFloat(quantity)).toFixed(2)
    : '';

  const [baseAsset, quoteAsset] = symbol?.split('-') || ['', ''];
  const baseBalance = balances.find(b => b.asset === baseAsset);
  const quoteBalance = balances.find(b => b.asset === quoteAsset);

  return (
    <div className="space-y-6">
      {/* Header */}
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
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Orderbook and Recent Trades */}
        <div className="lg:col-span-2 space-y-6">
          {/* Orderbook */}
          <div className="bg-slate-800 rounded-lg p-4">
            <h2 className="text-lg font-semibold text-white mb-4">Order Book</h2>
            <div className="grid grid-cols-2 gap-4">
              {/* Asks */}
              <div>
                <div className="text-xs text-slate-400 mb-2 grid grid-cols-3 gap-2">
                  <span>Price</span>
                  <span className="text-right">Amount</span>
                  <span className="text-right">Total</span>
                </div>
                <div className="space-y-1">
                  {orderbook?.asks.slice().reverse().map((ask, i) => (
                    <div key={i} className="text-sm grid grid-cols-3 gap-2 text-red-400">
                      <span>{ask.price.toFixed(2)}</span>
                      <span className="text-right">{ask.quantity.toFixed(2)}</span>
                      <span className="text-right text-slate-400">{ask.total.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Bids */}
              <div>
                <div className="text-xs text-slate-400 mb-2 grid grid-cols-3 gap-2">
                  <span>Price</span>
                  <span className="text-right">Amount</span>
                  <span className="text-right">Total</span>
                </div>
                <div className="space-y-1">
                  {orderbook?.bids.map((bid, i) => (
                    <div key={i} className="text-sm grid grid-cols-3 gap-2 text-green-400">
                      <span>{bid.price.toFixed(2)}</span>
                      <span className="text-right">{bid.quantity.toFixed(2)}</span>
                      <span className="text-right text-slate-400">{bid.total.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Recent Trades */}
          <div className="bg-slate-800 rounded-lg p-4">
            <h2 className="text-lg font-semibold text-white mb-4">Recent Trades</h2>
            <div className="space-y-2">
              {trades.slice(0, 10).map((trade) => (
                <div key={trade.id} className="flex justify-between text-sm">
                  <span className={trade.side === 'buy' ? 'text-green-400' : 'text-red-400'}>
                    {trade.price.toFixed(2)}
                  </span>
                  <span className="text-slate-300">{trade.quantity.toFixed(2)}</span>
                  <span className="text-slate-400">
                    {new Date(trade.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* My Open Orders */}
          <div className="bg-slate-800 rounded-lg p-4">
            <h2 className="text-lg font-semibold text-white mb-4">My Open Orders</h2>
            {myOrders.length === 0 ? (
              <p className="text-slate-400 text-sm">No open orders</p>
            ) : (
              <div className="space-y-2">
                {myOrders.map((order) => (
                  <div key={order.id} className="flex items-center justify-between text-sm border-b border-slate-700 pb-2">
                    <div>
                      <span className={order.side === 'buy' ? 'text-green-400' : 'text-red-400'}>
                        {order.side.toUpperCase()}
                      </span>
                      {' '}
                      <span className="text-slate-300">{order.type}</span>
                    </div>
                    <div className="text-slate-300">
                      {order.price?.toFixed(2)} × {order.quantity.toFixed(2)}
                    </div>
                    <button
                      onClick={() => cancelOrderMutation.mutate(order.id)}
                      className="text-red-400 hover:text-red-300"
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
        <div className="bg-slate-800 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-white mb-4">Place Order</h2>
          
          {/* Order Side */}
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setOrderSide('buy')}
              className={`flex-1 py-2 rounded ${
                orderSide === 'buy'
                  ? 'bg-green-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
              }`}
            >
              Buy
            </button>
            <button
              onClick={() => setOrderSide('sell')}
              className={`flex-1 py-2 rounded ${
                orderSide === 'sell'
                  ? 'bg-red-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
              }`}
            >
              Sell
            </button>
          </div>

          {/* Order Type */}
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setOrderType('limit')}
              className={`flex-1 py-2 rounded text-sm ${
                orderType === 'limit'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
              }`}
            >
              Limit
            </button>
            <button
              onClick={() => setOrderType('market')}
              className={`flex-1 py-2 rounded text-sm ${
                orderType === 'market'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
              }`}
            >
              Market
            </button>
          </div>

          {/* Price Input */}
          {orderType === 'limit' && (
            <div className="mb-4">
              <label className="block text-sm text-slate-400 mb-2">Price ({quoteAsset})</label>
              <input
                type="number"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="0.00"
                className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          )}

          {/* Quantity Input */}
          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-2">Amount ({baseAsset})</label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0.00"
              className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Total */}
          {orderType === 'limit' && total && (
            <div className="mb-4 p-3 bg-slate-700 rounded">
              <div className="flex justify-between text-sm">
                <span className="text-slate-400">Total</span>
                <span className="text-white">{total} {quoteAsset}</span>
              </div>
            </div>
          )}

          {/* Balances */}
          <div className="mb-4 p-3 bg-slate-700 rounded text-sm">
            <div className="flex justify-between mb-1">
              <span className="text-slate-400">Available {baseAsset}</span>
              <span className="text-white">{baseBalance?.available.toFixed(4) || '0.0000'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Available {quoteAsset}</span>
              <span className="text-white">{quoteBalance?.available.toFixed(2) || '0.00'}</span>
            </div>
          </div>

          {/* Submit Button */}
          <button
            onClick={handleSubmitOrder}
            disabled={!quantity || (orderType === 'limit' && !price) || createOrderMutation.isPending}
            className={`w-full py-3 rounded font-semibold ${
              orderSide === 'buy'
                ? 'bg-green-600 hover:bg-green-700 text-white'
                : 'bg-red-600 hover:bg-red-700 text-white'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {orderSide === 'buy' ? 'Buy' : 'Sell'} {baseAsset}
          </button>
        </div>
      </div>
    </div>
  );
}
