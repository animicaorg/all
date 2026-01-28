import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Search, TrendingUp, TrendingDown } from 'lucide-react';
import { apiClient } from '../lib/api-client';

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
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold text-white">Markets</h1>
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
                    ${market.lastPrice.toLocaleString()}
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
                    ${market.high24h.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-slate-300">
                    ${market.low24h.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-slate-300">
                    ${market.volume24h.toLocaleString()}
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
    </div>
  );
}
