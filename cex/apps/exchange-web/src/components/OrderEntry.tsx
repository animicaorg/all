import { useState, useMemo } from 'react';
import { toast } from 'react-hot-toast';
import { v4 as uuidv4 } from 'uuid';
import type { Market, Balance, CreateOrderRequest } from '../types';

interface OrderEntryProps {
  market: Market;
  balances: Balance[];
  onSubmit: (order: CreateOrderRequest) => Promise<any>;
  isSubmitting: boolean;
}

export function OrderEntry({ market, balances, onSubmit, isSubmitting }: OrderEntryProps) {
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [orderType, setOrderType] = useState<'limit' | 'market'>('limit');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');

  const baseBalance = balances.find((b) => b.asset === market.baseAsset);
  const quoteBalance = balances.find((b) => b.asset === market.quoteAsset);

  const availableBalance = useMemo(() => {
    if (side === 'buy') {
      return quoteBalance?.available || 0;
    } else {
      return baseBalance?.available || 0;
    }
  }, [side, baseBalance, quoteBalance]);

  const total = useMemo(() => {
    if (orderType === 'market' || !price || !quantity) return 0;
    return parseFloat(price) * parseFloat(quantity);
  }, [orderType, price, quantity]);

  const estimatedFee = useMemo(() => {
    if (!total) return 0;
    const feeBps = market.makerFeeBps || 10;
    return (total * feeBps) / 10000;
  }, [total, market.makerFeeBps]);

  const handleSetPercentage = (percent: number) => {
    if (!availableBalance) return;

    if (side === 'buy') {
      if (orderType === 'limit' && price) {
        const maxQty = (availableBalance * percent) / parseFloat(price);
        setQuantity(maxQty.toFixed(8));
      }
    } else {
      const qty = availableBalance * percent;
      setQuantity(qty.toFixed(8));
    }
  };

  const validate = (): string | null => {
    const qty = parseFloat(quantity);
    const pr = parseFloat(price);

    if (isNaN(qty) || qty <= 0) {
      return 'Invalid quantity';
    }

    if (orderType === 'limit' && (isNaN(pr) || pr <= 0)) {
      return 'Invalid price';
    }

    // Check min order size
    if (market.minOrderSize && qty < market.minOrderSize) {
      return `Minimum order size is ${market.minOrderSize}`;
    }

    // Check tick size (use epsilon for floating-point comparison)
    if (market.priceTick && orderType === 'limit') {
      const priceRemainder = pr % market.priceTick;
      const epsilon = market.priceTick / 1000;
      if (Math.abs(priceRemainder) > epsilon && Math.abs(priceRemainder - market.priceTick) > epsilon) {
        return `Price must be a multiple of ${market.priceTick}`;
      }
    }

    // Check step size (use epsilon for floating-point comparison)
    if (market.sizeStep) {
      const qtyRemainder = qty % market.sizeStep;
      const epsilon = market.sizeStep / 1000;
      if (Math.abs(qtyRemainder) > epsilon && Math.abs(qtyRemainder - market.sizeStep) > epsilon) {
        return `Quantity must be a multiple of ${market.sizeStep}`;
      }
    }

    // Check balance
    // Note: Fee calculation assumes fee is paid in quote asset
    // In production, check market.feeAsset and adjust accordingly
    if (side === 'buy') {
      const required = orderType === 'limit' ? total + estimatedFee : availableBalance;
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
      type: orderType,
      quantity: parseFloat(quantity),
      clientOrderId: uuidv4(),
      idempotencyKey: uuidv4(),
    };

    if (orderType === 'limit') {
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

  const canSubmit = () => {
    if (isSubmitting) return false;
    if (!quantity) return false;
    if (orderType === 'limit' && !price) return false;
    return validate() === null;
  };

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">Place Order</h2>

      {/* Order Side */}
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

      {/* Order Type */}
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setOrderType('limit')}
          className={`flex-1 py-2 rounded text-sm font-medium transition-colors ${
            orderType === 'limit'
              ? 'bg-blue-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Limit
        </button>
        <button
          onClick={() => setOrderType('market')}
          className={`flex-1 py-2 rounded text-sm font-medium transition-colors ${
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

      {/* Quantity Input */}
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

      {/* Percentage Buttons */}
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

      {/* Total & Fee Preview */}
      {orderType === 'limit' && total > 0 && (
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

      {/* Available Balance */}
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

      {/* Submit Button */}
      <button
        onClick={handleSubmit}
        disabled={!canSubmit()}
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
