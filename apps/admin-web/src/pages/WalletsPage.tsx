import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Save, Search, ToggleLeft, ToggleRight } from 'lucide-react';
import { apiClient, type AssetNetwork } from '../services/api';
import {
  Button,
  EmptyState,
  ErrorPanel,
  LoadingPanel,
  PageHeader,
  PaginationControls,
  Panel,
  PanelHeader,
  StatusBadge,
} from '../components/AdminUI';
import { useAuth } from '../contexts/AuthContext';
import { errorMessage, formatDecimal, formatNumber, shortId } from '../lib/format';

export default function WalletsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState('');
  const [purpose, setPurpose] = useState('');
  const [page, setPage] = useState(1);
  const [selectedNetwork, setSelectedNetwork] = useState<AssetNetwork | null>(null);
  const [networkForm, setNetworkForm] = useState({
    depositEnabled: true,
    withdrawalEnabled: true,
    minWithdrawal: '0',
    withdrawalFee: '0',
  });

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      provider: provider || undefined,
      purpose: purpose || undefined,
    }),
    [page, provider, purpose]
  );

  const walletsQuery = useQuery({
    queryKey: ['wallets', params],
    queryFn: () => apiClient.listWallets(params),
  });

  useEffect(() => {
    if (!selectedNetwork) return;
    setNetworkForm({
      depositEnabled: selectedNetwork.depositEnabled,
      withdrawalEnabled: selectedNetwork.withdrawalEnabled,
      minWithdrawal: selectedNetwork.minWithdrawal,
      withdrawalFee: selectedNetwork.withdrawalFee,
    });
  }, [selectedNetwork]);

  const invalidateWallets = async () => {
    await queryClient.invalidateQueries({ queryKey: ['wallets'] });
  };

  const walletMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) => apiClient.updateWallet(id, { isActive }),
    onSuccess: invalidateWallets,
  });

  const networkMutation = useMutation({
    mutationFn: (id: string) => apiClient.updateAssetNetwork(id, networkForm),
    onSuccess: async () => {
      setSelectedNetwork(null);
      await invalidateWallets();
    },
  });

  const wallets = walletsQuery.data?.data.wallets ?? [];
  const assetNetworks = walletsQuery.data?.data.assetNetworks ?? [];
  const pagination = walletsQuery.data?.data.pagination;

  return (
    <div className="space-y-6">
      <PageHeader title="Wallets" description="Provider wallets, asset networks, and transfer rail availability." />

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[180px_180px_auto]">
          <select
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value);
              setPage(1);
            }}
            className="field-input"
          >
            <option value="">All providers</option>
            <option value="BITGO">BitGo</option>
            <option value="LOCAL_ANIMICA">Local Animica</option>
            <option value="OTHER">Other</option>
          </select>
          <select
            value={purpose}
            onChange={(event) => {
              setPurpose(event.target.value);
              setPage(1);
            }}
            className="field-input"
          >
            <option value="">All purposes</option>
            <option value="HOT">Hot</option>
            <option value="WARM">Warm</option>
            <option value="COLD">Cold</option>
            <option value="TREASURY">Treasury</option>
            <option value="FEE">Fee</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => walletsQuery.refetch()}>
            <Search className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {walletsQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading wallets" />
          </div>
        ) : walletsQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(walletsQuery.error, 'Failed to load wallets.')} />
          </div>
        ) : wallets.length === 0 ? (
          <EmptyState title="No wallets found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Wallet</th>
                  <th className="px-5 py-3">Provider</th>
                  <th className="px-5 py-3">Purpose</th>
                  <th className="px-5 py-3">Network</th>
                  <th className="px-5 py-3">Assigned</th>
                  <th className="px-5 py-3">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {wallets.map((wallet) => (
                  <tr key={wallet.id}>
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{wallet.providerRef}</div>
                      <div className="text-xs text-gray-500">{wallet.address ?? shortId(wallet.id)}</div>
                    </td>
                    <td className="px-5 py-4 text-gray-700">{wallet.provider}</td>
                    <td className="px-5 py-4">
                      <StatusBadge value={wallet.purpose} />
                    </td>
                    <td className="px-5 py-4 text-gray-700">{wallet.network.code}</td>
                    <td className="px-5 py-4 text-gray-700">{formatNumber(wallet._count?.assignedAddresses ?? 0)}</td>
                    <td className="px-5 py-4">
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={!hasPermission('wallets:write') || walletMutation.isPending}
                        onClick={() => walletMutation.mutate({ id: wallet.id, isActive: !wallet.isActive })}
                      >
                        {wallet.isActive ? <ToggleRight className="h-4 w-4" /> : <ToggleLeft className="h-4 w-4" />}
                        {wallet.isActive ? 'Active' : 'Inactive'}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pagination && (
          <PaginationControls page={pagination.page} totalPages={pagination.totalPages} onPageChange={setPage} />
        )}
      </Panel>

      <Panel>
        <PanelHeader title="Asset Networks" />
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-5 py-3">Asset</th>
                <th className="px-5 py-3">Network</th>
                <th className="px-5 py-3">Deposits</th>
                <th className="px-5 py-3">Withdrawals</th>
                <th className="px-5 py-3">Min Withdrawal</th>
                <th className="px-5 py-3">Fee</th>
                <th className="px-5 py-3">Activity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {assetNetworks.map((item) => (
                <tr key={item.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setSelectedNetwork(item)}>
                  <td className="px-5 py-4">
                    <div className="font-medium text-gray-950">{item.asset.symbol}</div>
                    <div className="text-xs text-gray-500">{item.asset.name}</div>
                  </td>
                  <td className="px-5 py-4 text-gray-700">{item.network.code}</td>
                  <td className="px-5 py-4">
                    <StatusBadge value={item.depositEnabled} />
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge value={item.withdrawalEnabled} />
                  </td>
                  <td className="px-5 py-4 text-gray-700">{formatDecimal(item.minWithdrawal)}</td>
                  <td className="px-5 py-4 text-gray-700">{formatDecimal(item.withdrawalFee)}</td>
                  <td className="px-5 py-4 text-gray-700">
                    {formatNumber(item._count?.deposits ?? 0)} dep / {formatNumber(item._count?.withdrawals ?? 0)} wd
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      {selectedNetwork && (
        <Panel>
          <PanelHeader
            title="Asset Network Controls"
            description={`${selectedNetwork.asset.symbol} on ${selectedNetwork.network.code}`}
          />
          <div className="grid gap-5 p-5 md:grid-cols-2 xl:grid-cols-4">
            <label className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
              <span>Deposits</span>
              <input
                type="checkbox"
                checked={networkForm.depositEnabled}
                onChange={(event) => setNetworkForm((prev) => ({ ...prev, depositEnabled: event.target.checked }))}
                className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
              />
            </label>
            <label className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
              <span>Withdrawals</span>
              <input
                type="checkbox"
                checked={networkForm.withdrawalEnabled}
                onChange={(event) => setNetworkForm((prev) => ({ ...prev, withdrawalEnabled: event.target.checked }))}
                className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block font-medium text-gray-700">Minimum withdrawal</span>
              <input
                value={networkForm.minWithdrawal}
                onChange={(event) => setNetworkForm((prev) => ({ ...prev, minWithdrawal: event.target.value }))}
                className="field-input"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block font-medium text-gray-700">Withdrawal fee</span>
              <input
                value={networkForm.withdrawalFee}
                onChange={(event) => setNetworkForm((prev) => ({ ...prev, withdrawalFee: event.target.value }))}
                className="field-input"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2 border-t border-gray-200 px-5 py-4">
            <Button
              type="button"
              disabled={!hasPermission('wallets:write') || networkMutation.isPending}
              onClick={() => networkMutation.mutate(selectedNetwork.id)}
            >
              <Save className="h-4 w-4" />
              Save Controls
            </Button>
            <Button type="button" variant="secondary" onClick={() => setSelectedNetwork(null)}>
              Cancel
            </Button>
            {networkMutation.isError && (
              <ErrorPanel message={errorMessage(networkMutation.error, 'Asset network update failed.')} />
            )}
          </div>
        </Panel>
      )}

      {walletMutation.isError && <ErrorPanel message={errorMessage(walletMutation.error, 'Wallet update failed.')} />}
    </div>
  );
}
