import { useQuery } from '@tanstack/react-query';
import { Wallet, TrendingUp } from 'lucide-react';
import { apiClient } from '../lib/api-client';

export default function AccountPage() {
  const { data: balances = [], isLoading } = useQuery({
    queryKey: ['balances'],
    queryFn: () => apiClient.getBalances(),
    refetchInterval: 5000,
  });

  const totalValueUSD = balances.reduce((sum, balance) => {
    // Mock conversion - in production would use actual market prices
    const mockPrices: Record<string, number> = {
      USDT: 1,
      ANM: 1.25,
      BTC: 45000,
      ETH: 2800,
    };
    return sum + balance.total * (mockPrices[balance.asset] || 0);
  }, 0);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-slate-400">Loading account...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-white">Account</h1>

      {/* Total Balance Card */}
      <div className="bg-gradient-to-br from-blue-600 to-blue-800 rounded-lg p-6">
        <div className="flex items-center gap-2 text-blue-200 mb-2">
          <Wallet size={20} />
          <span className="text-sm font-medium">Total Balance</span>
        </div>
        <div className="text-4xl font-bold text-white mb-4">
          ${totalValueUSD.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        <div className="flex items-center gap-2 text-blue-200">
          <TrendingUp size={16} />
          <span className="text-sm">Estimated Value in USD</span>
        </div>
      </div>

      {/* Balances Table */}
      <div className="bg-slate-800 rounded-lg overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">Balances</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">
                  Asset
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  Total
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  Available
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-slate-300 uppercase tracking-wider">
                  In Orders
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {balances.map((balance) => (
                <tr key={balance.asset} className="hover:bg-slate-700 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <div className="w-8 h-8 bg-slate-600 rounded-full flex items-center justify-center text-sm font-bold text-white mr-3">
                        {balance.asset.charAt(0)}
                      </div>
                      <div>
                        <div className="text-sm font-medium text-white">{balance.asset}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-white font-medium">
                    {balance.total.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: balance.asset === 'BTC' || balance.asset === 'ETH' ? 8 : 2,
                    })}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-green-400">
                    {balance.available.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: balance.asset === 'BTC' || balance.asset === 'ETH' ? 8 : 2,
                    })}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm text-yellow-400">
                    {balance.locked.toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: balance.asset === 'BTC' || balance.asset === 'ETH' ? 8 : 2,
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Deposit/Withdraw Section (Placeholder) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-800 rounded-lg p-6">
          <h3 className="text-lg font-semibold text-white mb-4">Deposit</h3>
          <p className="text-slate-400 text-sm mb-4">
            Deposit functionality is currently under development. Please check back later.
          </p>
          <button
            disabled
            className="w-full py-2 px-4 bg-slate-700 text-slate-400 rounded-lg cursor-not-allowed"
          >
            Coming Soon
          </button>
        </div>

        <div className="bg-slate-800 rounded-lg p-6">
          <h3 className="text-lg font-semibold text-white mb-4">Withdraw</h3>
          <p className="text-slate-400 text-sm mb-4">
            Withdrawal functionality is currently under development. Please check back later.
          </p>
          <button
            disabled
            className="w-full py-2 px-4 bg-slate-700 text-slate-400 rounded-lg cursor-not-allowed"
          >
            Coming Soon
          </button>
        </div>
      </div>
    </div>
  );
}
