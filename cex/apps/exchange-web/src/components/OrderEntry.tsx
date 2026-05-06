import { useState, useMemo } from 'react';
import { toast } from 'react-hot-toast';
import { v4 as uuidv4 } from 'uuid';
import type { Market, Balance, CreateOrderRequest } from '../types';

interface OrderEntryProps {
  market: Market;
  balances: Balance[];
  onSubmit: (order: CreateOrderRequest) => Promise<any>;
  isSubmitting: boolean;
  referencePrice?: number;
}

type OrderMode = 'limit' | 'market';

function stepDecimals(step?: number): number {
  if (!step || step <= 0) return 8;
  const fixed = step.toString();
  if (fixed.includes('e-')) return Number(fixed.split('e-')[1]);
  const fraction = fixed.split('.')[1];
  return fraction ? fraction.length : 0;
}

function roundToStep(value: number, step: number | undefined, direction: 'down' | 'up'): number {
  if (!step || step <= 0) return value;
  const scaled = value / step;
  return (direction === 'down' ? Math.floor(scaled) : Math.ceil(scaled)) * step;
}

export function OrderEntry({ market, balances, onSubmit, isSubmitting }: OrderEntryProps) {
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [orderMode, setOrderMode] = useState<OrderMode>('limit');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');

  const baseBalance = balances.find((b) => b.asset === market.baseAsset);
  const quoteBalance = balances.find((b) => b.asset === market.quoteAsset);
  const priceDecimals = stepDecimals(market.priceTick);

  const availableBalance = useMemo(() => {
    return side === 'buy'
      ? (quoteBalance?.available || 0)
      : (baseBalance?.available || 0);
  }, [side, baseBalance, quoteBalance]);

  const total = useMemo(() => {
    if (orderMode !== 'limit' || !price || !quantity) return 0;
    const pr = parseFloat(price);
    const qty = parseFloat(quantity);
    if (!Number.isFinite(pr) || !Number.isFinite(qty)) return 0;
    return pr * qty;
  }, [orderMode, price, quantity]);

  const estimatedFee = useMemo(() => {
    if (!total) return 0;
    const feeBps = market.makerFeeBps || 10;
    return (total * feeBps) / 10000;
  }, [total, market.makerFeeBps]);

  const handleSetPercentage = (percent: number) => {
    if (!availableBalance) return;

    if (side === 'buy') {
      if (orderMode === 'limit' && price) {
        const pr = parseFloat(price);
        if (!Number.isFinite(pr) || pr <= 0) return;
        const maxQty = (availableBalance * percent) / pr;
        const rounded = roundToStep(maxQty, market.sizeStep, 'down');
        setQuantity(rounded.toFixed(8));
      }
    } else {
      const qty = roundToStep(availableBalance * percent, market.sizeStep, 'down');
      setQuantity(qty.toFixed(8));
    }
  };

  const validate = (): string | null => {
    const qty = parseFloat(quantity);
    const pr = parseFloat(price);

    if (isNaN(qty) || qty <= 0) {
      return 'Invalid quantity';
    }

    if (orderMode === 'limit' && (isNaN(pr) || pr <= 0)) {
      return 'Invalid price';
    }

    if (market.minOrderSize && qty < market.minOrderSize) {
      return `Minimum order size is ${market.minOrderSize}`;
    }

    if (market.priceTick && orderMode === 'limit') {
      const priceRemainder = pr % market.priceTick;
      const epsilon = market.priceTick / 1000;
      if (Math.abs(priceRemainder) > epsilon && Math.abs(priceRemainder - market.priceTick) > epsilon) {
        return `Price must be a multiple of ${market.priceTick}`;
      }
    }

    if (market.sizeStep) {
      const qtyRemainder = qty % market.sizeStep;
      const epsilon = market.sizeStep / 1000;
      if (Math.abs(qtyRemainder) > epsilon && Math.abs(qtyRemainder - market.sizeStep) > epsilon) {
        return `Quantity must be a multiple of ${market.sizeStep}`;
      }
    }

    if (side === 'buy') {
      const required = orderMode === 'limit' ? total + estimatedFee : availableBalance;
      if (required > availableBalance) {
        return 'Insufficient balance';
      }
    } else {
      if (qty > availableBalance) {
        return 'Insufficient balance';
      }
    }

    return null;
  };

  const handleSubmit = async () => {
    const error = validate();
    if (error) {
      toast.error(error);
      return;
    }

    const order: CreateOrderRequest = {
      symbol: market.symbol,
      side,
      type: orderMode,
      quantity: parseFloat(quantity),
      clientOrderId: uuidv4(),
      idempotencyKey: uuidv4(),
    };

    if (orderMode === 'limit') {
      order.price = parseFloat(price);
    }

    try {
      await onSubmit(order);
      toast.success(`${side === 'buy' ? 'Buy' : 'Sell'} order placed`);
      setPrice('');
      setQuantity('');
    } catch (error: any) {
      toast.error(error.message || 'Failed to place order');
    }
  };

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">Place Order</h2>

      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setSide('buy')}
          className={`flex-1 py-2 rounded font-medium transition-colors ${
            side === 'buy'
              ? 'bg-green-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Buy
        </button>
        <button
          onClick={() => setSide('sell')}
          className={`flex-1 py-2 rounded font-medium transition-colors ${
            side === 'sell'
              ? 'bg-red-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Sell
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-4">
        <button
          onClick={() => setOrderMode('limit')}
          className={`flex-1 py-2 rounded text-sm font-medium transition-colors ${
            orderMode === 'limit'
              ? 'bg-blue-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Limit
        </button>
        <button
          onClick={() => setOrderMode('market')}
          className={`flex-1 py-2 rounded text-sm font-medium transition-colors ${
            orderMode === 'market'
              ? 'bg-blue-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Market
        </button>
      </div>

      {orderMode === 'limit' && (
        <div className="mb-4">
          <label className="block text-sm text-slate-400 mb-2">
            Price ({market.quoteAsset})
          </label>
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="0.00"
            step={market.priceTick || 0.01}
            className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}

      <div className="mb-4">
        <label className="block text-sm text-slate-400 mb-2">
          Amount ({market.baseAsset})
        </label>
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          placeholder="0.00"
          step={market.sizeStep || 0.001}
          className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      <div className="grid grid-cols-4 gap-2 mb-4">
        {[0.25, 0.5, 0.75, 1.0].map((percent) => (
          <button
            key={percent}
            onClick={() => handleSetPercentage(percent)}
            className="py-1 text-sm bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors"
          >
            {percent * 100}%
          </button>
        ))}
      </div>

      {orderMode === 'limit' && total > 0 && (
        <div className="mb-4 p-3 bg-slate-700 rounded space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Total</span>
            <span className="text-white font-medium">
              {total.toFixed(2)} {market.quoteAsset}
            </span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Est. Fee ({(market.makerFeeBps || 10) / 100}%)</span>
            <span className="text-white">
              {estimatedFee.toFixed(4)} {market.quoteAsset}
            </span>
          </div>
        </div>
      )}

      <div className="mb-4 p-3 bg-slate-700 rounded">
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">
            Available {side === 'buy' ? market.quoteAsset : market.baseAsset}
          </span>
          <span className="text-white font-medium">
            {availableBalance.toFixed(side === 'buy' ? 2 : 8)}
          </span>
        </div>
      </div>

      <button
        onClick={handleSubmit}
        disabled={isSubmitting}
        className={`w-full py-3 rounded font-semibold transition-colors ${
          side === 'buy'
            ? 'bg-green-600 hover:bg-green-700 text-white'
            : 'bg-red-600 hover:bg-red-700 text-white'
        } disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {isSubmitting ? 'Placing...' : `${side === 'buy' ? 'Buy' : 'Sell'} ${market.baseAsset}`}
      </button>
    </div>
  );
}
