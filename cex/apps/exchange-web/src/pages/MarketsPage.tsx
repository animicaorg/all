import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Search, TrendingUp, TrendingDown } from 'lucide-react';
import { apiClient } from '../lib/api-client';
import { Seo } from '../components/Seo';
import { breadcrumbJsonLd } from '../lib/seo';

export default function MarketsPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const navigate = useNavigate();

  const { data: markets = [], isLoading } = useQuery({
    queryKey: ['markets'],
    queryFn: () => apiClient.getMarkets(),
    refetchInterval: 5000,
  });

  const filteredMarkets = markets.filter((market) =>
    market.symbol.toLowerCase().includes(searchTerm.toLowerCase()) ||
    market.baseAsset.toLowerCase().includes(searchTerm.toLowerCase()) ||
    market.quoteAsset.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleMarketClick = (symbol: string) => {
    navigate(`/trade/${symbol}`);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-slate-400">Loading markets...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Seo
        title="ANM Markets | Trade BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM"
        description="Explore live Animica Exchange markets for ANM trading pairs including BTC/ANM, DOGE/ANM, LTC/ANM, and ZEC/ANM."
        path="/markets"
        structuredData={[
          breadcrumbJsonLd([
            { name: 'Home', path: '/' },
            { name: 'Markets', path: '/markets' },
          ]),
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
        <div>
          <p className="mb-2 text-sm font-semibold uppercase tracking-wider text-blue-300">Live ANM markets</p>
          <h1 className="text-3xl font-bold text-white">Animica Exchange Markets</h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
            Explore live ANM trading pairs, review quote-asset pricing, and open a pair page for the order book, recent trades, USD estimates, and order entry.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => navigate('/airdrop')}
            className="rounded-md border border-slate-600 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
          >
            Claim ANM
          </button>
          <button
            type="button"
            onClick={() => navigate('/register')}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500"
          >
            Create Account
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400" size={20} />
        <input
          type="text"
          placeholder="Search markets..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {/* Markets table */}
      <div className="bg-slate-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">
                  Symbol
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  Last Price
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  24h Change
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  24h High
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  24h Low
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  24h Volume
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {filteredMarkets.map((market) => (
                <tr
                  key={market.symbol}
                  onClick={() => handleMarketClick(market.symbol)}
                  className="hover:bg-slate-700 cursor-pointer transition-colors"
                >
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <div>
                        <div className="text-sm font-medium text-white">{market.symbol}</div>
                        <div className="text-xs text-slate-400">
                          {market.baseAsset}/{market.quoteAsset}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-white">
                    {market.lastPrice.toLocaleString()} {market.quoteAsset}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                    <div className={`flex items-center justify-end gap-1 ${
                      market.change24h >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {market.change24h >= 0 ? (
                        <TrendingUp size={16} />
                      ) : (
                        <TrendingDown size={16} />
                      )}
                      <span>{market.change24h >= 0 ? '+' : ''}{market.change24h.toFixed(2)}%</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-slate-300">
                    {market.high24h.toLocaleString()} {market.quoteAsset}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-slate-300">
                    {market.low24h.toLocaleString()} {market.quoteAsset}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-slate-300">
                    {market.volume24h.toLocaleString()} {market.baseAsset}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {filteredMarkets.length === 0 && (
        <div className="text-center py-12 text-slate-400">
          No markets found matching "{searchTerm}"
        </div>
      )}

      <section className="rounded-lg bg-slate-800 p-6">
        <h2 className="text-xl font-semibold text-white">Trade ANM on live pair pages</h2>
        <p className="mt-3 text-sm leading-6 text-slate-300">
          Animica Exchange pair pages are public for market research. Sign in or create an account when you are ready to claim ANM, deposit supported assets, or place an order.
        </p>
        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          {['BTC-ANM', 'DOGE-ANM', 'LTC-ANM', 'ZEC-ANM'].map((symbol) => (
            <button
              key={symbol}
              type="button"
              onClick={() => navigate(`/trade/${symbol}`)}
              className="rounded-md border border-slate-600 px-3 py-2 font-semibold text-slate-100 hover:bg-slate-700"
            >
              {symbol.replace('-', '/')}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
