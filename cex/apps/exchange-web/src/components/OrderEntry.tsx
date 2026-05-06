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

type OrderMode = 'limit' | 'market' | 'liquidity';

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

export function OrderEntry({ market, balances, onSubmit, isSubmitting, referencePrice }: OrderEntryProps) {
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [orderMode, setOrderMode] = useState<OrderMode>('limit');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');
  const [spreadBps, setSpreadBps] = useState('50');

  const baseBalance = balances.find((b) => b.asset === market.baseAsset);
  const quoteBalance = balances.find((b) => b.asset === market.quoteAsset);
  const priceDecimals = stepDecimals(market.priceTick);

  const availableBalance = useMemo(() => {
    if (side === 'buy') {
      return quoteBalance?.available || 0;
    } else {
      return baseBalance?.available || 0;
    }
  }, [side, baseBalance, quoteBalance]);

  const total = useMemo(() => {
    if (orderMode !== 'limit' || !price || !quantity) return 0;
    return parseFloat(price) * parseFloat(quantity);
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
        const maxQty = (availableBalance * percent) / parseFloat(price);
        setQuantity(maxQty.toFixed(8));
      }
    } else {
      const qty = availableBalance * percent;
      setQuantity(qty.toFixed(8));
    }
  };

  const liquidityQuote = useMemo(() => {
    const center = Number.isFinite(parseFloat(price)) && parseFloat(price) > 0
      ? parseFloat(price)
      : referencePrice || market.lastPrice || 0;
    const spread = parseFloat(spreadBps);
    const qty = parseFloat(quantity);

    if (!center || !Number.isFinite(spread) || spread <= 0) {
      return null;
    }

    const halfSpread = spread / 20_000;
    const bid = roundToStep(center * (1 - halfSpread), market.priceTick, 'down');
    const ask = roundToStep(center * (1 + halfSpread), market.priceTick, 'up');

    return {
      center,
      bid,
      ask,
      quantity: Number.isFinite(qty) ? qty : 0,
      buyTotal: Number.isFinite(qty) ? bid * qty : 0,
      sellTotal: Number.isFinite(qty) ? ask * qty : 0,
    };
  }, [market.lastPrice, market.priceTick, price, quantity, referencePrice, spreadBps]);

  const validate = (): string | null => {
    const qty = parseFloat(quantity);
    const pr = parseFloat(price);

    if (isNaN(qty) || qty <= 0) {
      return 'Invalid quantity';
    }

    if (orderMode === 'limit' && (isNaN(pr) || pr <= 0)) {
      return 'Invalid price';
    }

    if (orderMode === 'liquidity') {
      if (!liquidityQuote || liquidityQuote.bid <= 0 || liquidityQuote.ask <= 0) {
        return 'Invalid center price';
      }
      if (liquidityQuote.bid >= liquidityQuote.ask) {
        return 'Spread is too small for the price tick';
      }
    }

    // Check min order size
    if (market.minOrderSize && qty < market.minOrderSize) {
      return `Minimum order size is ${market.minOrderSize}`;
    }

    // Check tick size (use epsilon for floating-point comparison)
    if (market.priceTick && orderMode === 'limit') {
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
    if (orderMode === 'liquidity') {
      const makerFee = liquidityQuote ? (liquidityQuote.buyTotal * (market.makerFeeBps || 10)) / 10000 : 0;
      const requiredQuote = (liquidityQuote?.buyTotal || 0) + makerFee;
      if (requiredQuote > (quoteBalance?.available || 0)) {
        return `Insufficient ${market.quoteAsset} balance`;
      }
      if (qty > (baseBalance?.available || 0)) {
        return `Insufficient ${market.baseAsset} balance`;
      }
    } else if (side === 'buy') {
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

    if (orderMode === 'liquidity') {
      if (!liquidityQuote) return;

      const baseOrder = {
        symbol: market.symbol,
        type: 'post_only' as const,
        quantity: parseFloat(quantity),
      };

      try {
        await onSubmit({
          ...baseOrder,
          side: 'buy',
          price: Number(liquidityQuote.bid.toFixed(priceDecimals)),
          clientOrderId: uuidv4(),
          idempotencyKey: uuidv4(),
        });
        await onSubmit({
          ...baseOrder,
          side: 'sell',
          price: Number(liquidityQuote.ask.toFixed(priceDecimals)),
          clientOrderId: uuidv4(),
          idempotencyKey: uuidv4(),
        });
        toast.success('Liquidity orders placed');
        setQuantity('');
      } catch (error: any) {
        toast.error(error.message || 'Failed to add liquidity');
      }
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

      {/* Order Side */}
      {orderMode !== 'liquidity' && (
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
      )}

      {/* Order Type */}
      <div className="grid grid-cols-3 gap-2 mb-4">
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
        <button
          onClick={() => setOrderMode('liquidity')}
          className={`flex-1 py-2 rounded text-sm font-medium transition-colors ${
            orderMode === 'liquidity'
              ? 'bg-blue-600 text-white'
              : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
          }`}
        >
          Add Liquidity
        </button>
      </div>

      {/* Price Input */}
      {(orderMode === 'limit' || orderMode === 'liquidity') && (
        <div className="mb-4">
          <label className="block text-sm text-slate-400 mb-2">
            {orderMode === 'liquidity' ? 'Center Price' : 'Price'} ({market.quoteAsset})
          </label>
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder={orderMode === 'liquidity' && referencePrice ? referencePrice.toFixed(priceDecimals) : '0.00'}
            step={market.priceTick || 0.01}
            className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}

      {orderMode === 'liquidity' && (
        <div className="mb-4">
          <label className="block text-sm text-slate-400 mb-2">
            Spread (bps)
          </label>
          <input
            type="number"
            value={spreadBps}
            onChange={(e) => setSpreadBps(e.target.value)}
            placeholder="50"
            min="1"
            step="1"
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
      {orderMode !== 'liquidity' && (
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
      )}

      {/* Total & Fee Preview */}
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

      {orderMode === 'liquidity' && liquidityQuote && (
        <div className="mb-4 p-3 bg-slate-700 rounded space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Bid</span>
            <span className="text-green-400 font-medium">
              {liquidityQuote.bid.toFixed(priceDecimals)} {market.quoteAsset}
            </span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Ask</span>
            <span className="text-red-400 font-medium">
              {liquidityQuote.ask.toFixed(priceDecimals)} {market.quoteAsset}
            </span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Quote Needed</span>
            <span className="text-white">
              {liquidityQuote.buyTotal.toFixed(8)} {market.quoteAsset}
            </span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-400">Base Needed</span>
            <span className="text-white">
              {(liquidityQuote.quantity || 0).toFixed(8)} {market.baseAsset}
            </span>
          </div>
        </div>
      )}

      {/* Available Balance */}
      <div className="mb-4 p-3 bg-slate-700 rounded">
        <div className="flex justify-between text-sm">
          <span className="text-slate-400">
            Available {orderMode === 'liquidity' ? `${market.quoteAsset} / ${market.baseAsset}` : side === 'buy' ? market.quoteAsset : market.baseAsset}
          </span>
          <span className="text-white font-medium">
            {orderMode === 'liquidity'
              ? `${(quoteBalance?.available || 0).toFixed(2)} / ${(baseBalance?.available || 0).toFixed(8)}`
              : availableBalance.toFixed(side === 'buy' ? 2 : 8)}
          </span>
        </div>
      </div>

      {/* Submit Button */}
      <button
        onClick={handleSubmit}
        disabled={isSubmitting}
        className={`w-full py-3 rounded font-semibold transition-colors ${
          side === 'buy'
            ? 'bg-green-600 hover:bg-green-700 text-white'
            : 'bg-red-600 hover:bg-red-700 text-white'
        } disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {isSubmitting
          ? 'Placing...'
          : orderMode === 'liquidity'
            ? `Add ${market.baseAsset} Liquidity`
            : `${side === 'buy' ? 'Buy' : 'Sell'} ${market.baseAsset}`}
      </button>
    </div>
  );
}
