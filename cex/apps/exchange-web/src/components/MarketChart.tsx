import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import { apiClient } from '../lib/api-client';
import type { Candle, Market } from '../types';

const RESOLUTIONS = ['1m', '5m', '15m', '1h', '4h', '1d'];

type ChartTrade = {
  price: number;
  quantity: number;
  timestamp: number;
};

function formatPrice(value: number, market: Market): string {
  const decimals = market.priceTick ? Math.max(0, Math.min(8, Math.round(Math.log10(1 / market.priceTick)))) : 2;
  return value.toFixed(decimals);
}

function candlesFromTrades(trades: ChartTrade[], resolution: string): Candle[] {
  const bucketMsByResolution: Record<string, number> = {
    '1m': 60 * 1000,
    '5m': 5 * 60 * 1000,
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '4h': 4 * 60 * 60 * 1000,
    '1d': 24 * 60 * 60 * 1000,
  };
  const bucketMs = bucketMsByResolution[resolution] ?? bucketMsByResolution['1m'];
  const orderedTrades = [...trades].sort((a, b) => a.timestamp - b.timestamp);
  const candleMap = new Map<number, Candle>();

  for (const trade of orderedTrades) {
    const bucket = Math.floor(trade.timestamp / bucketMs) * bucketMs;
    const candle = candleMap.get(bucket);

    if (!candle) {
      candleMap.set(bucket, {
        timestamp: bucket,
        open: trade.price,
        high: trade.price,
        low: trade.price,
        close: trade.price,
        volume: trade.quantity,
      });
      continue;
    }

    candle.high = Math.max(candle.high, trade.price);
    candle.low = Math.min(candle.low, trade.price);
    candle.close = trade.price;
    candle.volume += trade.quantity;
  }

  return [...candleMap.values()];
}

export function MarketChart({ symbol, market, trades }: { symbol: string; market: Market; trades: ChartTrade[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const [resolution, setResolution] = useState('1m');

  const { data: candles = [], isLoading } = useQuery({
    queryKey: ['candles', symbol, resolution],
    queryFn: () => apiClient.getCandles(symbol, resolution, 300),
    refetchInterval: 15000,
  });

  const chartCandles = useMemo(() => {
    return candles.length > 0 ? candles : candlesFromTrades(trades, resolution);
  }, [candles, trades, resolution]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 360,
      layout: {
        background: { color: '#0f172a' },
        textColor: '#cbd5e1',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: '#334155',
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 1,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      priceFormat: {
        type: 'price',
        precision: market.priceTick ? Math.max(0, Math.min(8, Math.round(Math.log10(1 / market.priceTick)))) : 2,
        minMove: market.priceTick ?? 0.01,
      },
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      color: '#38bdf8',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, [market.priceTick]);

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;

    const candleData = chartCandles.map((candle) => ({
      time: Math.floor(candle.timestamp / 1000) as UTCTimestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    }));
    const volumeData = chartCandles.map((candle) => ({
      time: Math.floor(candle.timestamp / 1000) as UTCTimestamp,
      value: candle.volume,
      color: candle.close >= candle.open ? '#22c55e55' : '#ef444455',
    }));

    candleSeriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volumeData);

    if (candleData.length > 0) {
      chartRef.current?.timeScale().fitContent();
    }
  }, [chartCandles]);

  const latestCandle = chartCandles[chartCandles.length - 1];

  return (
    <div className="bg-slate-800 rounded-lg overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-slate-700 px-4 py-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">Price Chart</h2>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-400">
            <span>O {latestCandle ? formatPrice(latestCandle.open, market) : '-'}</span>
            <span>H {latestCandle ? formatPrice(latestCandle.high, market) : '-'}</span>
            <span>L {latestCandle ? formatPrice(latestCandle.low, market) : '-'}</span>
            <span>C {latestCandle ? formatPrice(latestCandle.close, market) : '-'}</span>
          </div>
        </div>
        <div className="flex rounded-md border border-slate-700 bg-slate-900 p-1">
          {RESOLUTIONS.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setResolution(item)}
              className={`min-w-10 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                resolution === item ? 'bg-blue-600 text-white' : 'text-slate-300 hover:bg-slate-700'
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      <div className="relative h-[360px]">
        <div ref={containerRef} className="h-full w-full" />
        {!isLoading && chartCandles.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-900/40 text-sm text-slate-400">
            No trades yet
          </div>
        )}
      </div>
    </div>
  );
}
