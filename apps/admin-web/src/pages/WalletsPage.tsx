import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Save, Search, ToggleLeft, ToggleRight } from 'lucide-react';
import { apiClient, type AssetNetwork, type ProviderSetupRequest, type WalletProvider } from '../services/api';
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

type ConfigurableProvider = ProviderSetupRequest['provider'];

const providerChoices: Array<{ value: ConfigurableProvider; label: string }> = [
  { value: 'BITGO', label: 'BitGo' },
  { value: 'ANIMICA_NODE', label: 'Animica node' },
  { value: 'BITCOIN_NODE', label: 'Bitcoin-style node' },
];

function providerLabel(provider: WalletProvider | string) {
  const labels: Record<string, string> = {
    BITGO: 'BitGo',
    ANIMICA_NODE: 'Animica node',
    BITCOIN_NODE: 'Bitcoin-style node',
    LOCAL_ANIMICA: 'Animica node',
    OTHER: 'Other',
  };
  return labels[provider] ?? provider.replace(/_/g, ' ');
}

function isCompatibleAssetNetwork(item: AssetNetwork, provider: ConfigurableProvider) {
  const networkKind = item.network.kind.toUpperCase();
  const networkCode = item.network.code.toUpperCase();
  const assetSymbol = item.asset.symbol.toUpperCase();

  if (provider === 'ANIMICA_NODE') {
    return assetSymbol === 'ANM' || networkCode === 'ANIMICA' || networkKind === 'ANIMICA' || networkKind === 'ACCOUNT';
  }
  if (provider === 'BITCOIN_NODE') {
    return networkCode === 'BTC' || networkKind === 'UTXO' || item.provider === 'BITCOIN_NODE';
  }
  return true;
}

function defaultBitgoCoin(item: AssetNetwork) {
  return item.bitgoCoin || item.asset.symbol.toLowerCase();
}

function defaultNodeWalletId(provider: ConfigurableProvider, item: AssetNetwork) {
  if (provider === 'ANIMICA_NODE') return `animica-node:${item.network.code}:${item.asset.symbol}`;
  if (provider === 'BITCOIN_NODE') return `bitcoin-node:${item.network.code}:${item.asset.symbol}`;
  return '';
}

export default function WalletsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState('');
  const [purpose, setPurpose] = useState('');
  const [page, setPage] = useState(1);
  const [selectedNetwork, setSelectedNetwork] = useState<AssetNetwork | null>(null);
  const [setupProvider, setSetupProvider] = useState<ConfigurableProvider>('BITGO');
  const [setupAssetNetworkId, setSetupAssetNetworkId] = useState('');
  const [setupMessage, setSetupMessage] = useState<string | null>(null);
  const [setupForm, setSetupForm] = useState({
    walletId: '',
    assetName: '',
    bitgoCoin: '',
    rpcUrl: '',
    address: '',
    depositEnabled: true,
    withdrawalEnabled: true,
  });
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

  const wallets = walletsQuery.data?.data.wallets ?? [];
  const assetNetworks = walletsQuery.data?.data.assetNetworks ?? [];
  const pagination = walletsQuery.data?.data.pagination;

  const compatibleAssetNetworks = useMemo(
    () => assetNetworks.filter((item) => isCompatibleAssetNetwork(item, setupProvider)),
    [assetNetworks, setupProvider]
  );

  const selectedSetupNetwork = useMemo(
    () =>
      compatibleAssetNetworks.find((item) => item.id === setupAssetNetworkId) ??
      compatibleAssetNetworks[0] ??
      null,
    [compatibleAssetNetworks, setupAssetNetworkId]
  );

  useEffect(() => {
    if (!selectedNetwork) return;
    setNetworkForm({
      depositEnabled: selectedNetwork.depositEnabled,
      withdrawalEnabled: selectedNetwork.withdrawalEnabled,
      minWithdrawal: selectedNetwork.minWithdrawal,
      withdrawalFee: selectedNetwork.withdrawalFee,
    });
  }, [selectedNetwork]);

  useEffect(() => {
    if (!selectedSetupNetwork) {
      setSetupAssetNetworkId('');
      return;
    }
    if (selectedSetupNetwork.id !== setupAssetNetworkId) {
      setSetupAssetNetworkId(selectedSetupNetwork.id);
    }
  }, [selectedSetupNetwork, setupAssetNetworkId]);

  useEffect(() => {
    if (!selectedSetupNetwork) return;
    const existingWallet = wallets.find(
      (wallet) => wallet.assetNetworkId === selectedSetupNetwork.id && wallet.provider === setupProvider
    );
    setSetupForm({
      walletId: existingWallet?.providerRef ?? defaultNodeWalletId(setupProvider, selectedSetupNetwork),
      assetName: selectedSetupNetwork.asset.name,
      bitgoCoin: defaultBitgoCoin(selectedSetupNetwork),
      rpcUrl: selectedSetupNetwork.rpcUrl ?? selectedSetupNetwork.network.rpcUrl ?? '',
      address: existingWallet?.address ?? '',
      depositEnabled: selectedSetupNetwork.depositEnabled,
      withdrawalEnabled: selectedSetupNetwork.withdrawalEnabled,
    });
  }, [selectedSetupNetwork, setupProvider, wallets]);

  const invalidateWallets = async () => {
    await queryClient.invalidateQueries({ queryKey: ['wallets'] });
  };

  const walletMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) => apiClient.updateWallet(id, { isActive }),
    onSuccess: invalidateWallets,
  });

  const setupMutation = useMutation({
    mutationFn: () => {
      if (!selectedSetupNetwork) {
        throw new Error('Select an asset network first.');
      }

      const payload: ProviderSetupRequest = {
        assetNetworkId: selectedSetupNetwork.id,
        provider: setupProvider,
        walletId: setupForm.walletId.trim() || undefined,
        assetName: setupProvider === 'BITCOIN_NODE' ? setupForm.assetName.trim() || null : null,
        address: setupForm.address.trim() || null,
        rpcUrl: setupProvider === 'BITGO' ? null : setupForm.rpcUrl.trim(),
        bitgoCoin: setupProvider === 'BITGO' ? setupForm.bitgoCoin.trim() : null,
        depositEnabled: setupForm.depositEnabled,
        withdrawalEnabled: setupForm.withdrawalEnabled,
      };

      return apiClient.configureWalletProvider(payload);
    },
    onSuccess: async () => {
      setSetupMessage('Provider setup saved.');
      await invalidateWallets();
    },
  });

  const networkMutation = useMutation({
    mutationFn: (id: string) => apiClient.updateAssetNetwork(id, networkForm),
    onSuccess: async () => {
      setSelectedNetwork(null);
      await invalidateWallets();
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Wallets" description="Custody provider setup, asset network controls, and transfer rails." />

      <Panel>
        <PanelHeader title="Provider Setup" description="Configure one asset network at a time." />
        <div className="space-y-5 p-5">
          <div className="flex flex-wrap gap-2">
            {providerChoices.map((choice) => (
              <Button
                key={choice.value}
                type="button"
                variant={setupProvider === choice.value ? 'primary' : 'secondary'}
                onClick={() => {
                  setSetupProvider(choice.value);
                  setSetupMessage(null);
                }}
              >
                {choice.label}
              </Button>
            ))}
          </div>

          {compatibleAssetNetworks.length === 0 ? (
            <EmptyState title="No compatible asset networks found" />
          ) : (
            <>
              <div className="grid gap-4 lg:grid-cols-[minmax(260px,1.15fr)_minmax(240px,0.85fr)]">
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="text-sm">
                    <span className="mb-1 block font-medium text-gray-700">Asset network</span>
                    <select
                      value={selectedSetupNetwork?.id ?? ''}
                      onChange={(event) => setSetupAssetNetworkId(event.target.value)}
                      className="field-input"
                    >
                      {compatibleAssetNetworks.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.asset.symbol} on {item.network.code}
                        </option>
                      ))}
                    </select>
                  </label>

                  {setupProvider === 'BITGO' ? (
                    <label className="text-sm">
                      <span className="mb-1 block font-medium text-gray-700">BitGo coin</span>
                      <input
                        value={setupForm.bitgoCoin}
                        onChange={(event) => setSetupForm((prev) => ({ ...prev, bitgoCoin: event.target.value }))}
                        className="field-input"
                        placeholder="btc"
                      />
                    </label>
                  ) : (
                    <label className="text-sm">
                      <span className="mb-1 block font-medium text-gray-700">RPC URL</span>
                      <input
                        value={setupForm.rpcUrl}
                        onChange={(event) => setSetupForm((prev) => ({ ...prev, rpcUrl: event.target.value }))}
                        className="field-input"
                        placeholder="http://user:pass@127.0.0.1:8332"
                      />
                    </label>
                  )}

                  {setupProvider === 'BITCOIN_NODE' && (
                    <label className="text-sm">
                      <span className="mb-1 block font-medium text-gray-700">Asset name</span>
                      <input
                        value={setupForm.assetName}
                        onChange={(event) => setSetupForm((prev) => ({ ...prev, assetName: event.target.value }))}
                        className="field-input"
                        placeholder={selectedSetupNetwork?.asset.name || selectedSetupNetwork?.asset.symbol || 'Bitcoin'}
                      />
                    </label>
                  )}

                  <label className="text-sm">
                    <span className="mb-1 block font-medium text-gray-700">
                      {setupProvider === 'BITGO' ? 'BitGo wallet ID' : 'Node wallet reference'}
                    </span>
                    <input
                      value={setupForm.walletId}
                      onChange={(event) => setSetupForm((prev) => ({ ...prev, walletId: event.target.value }))}
                      className="field-input"
                      placeholder={setupProvider === 'BITGO' ? 'BitGo wallet ID' : defaultNodeWalletId(setupProvider, selectedSetupNetwork!)}
                    />
                  </label>

                  <label className="text-sm">
                    <span className="mb-1 block font-medium text-gray-700">Hot wallet address</span>
                    <input
                      value={setupForm.address}
                      onChange={(event) => setSetupForm((prev) => ({ ...prev, address: event.target.value }))}
                      className="field-input"
                      placeholder="Optional"
                    />
                  </label>
                </div>

                <div className="rounded-md border border-gray-200 p-4 text-sm">
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                    <label className="flex items-center justify-between gap-3">
                      <span className="font-medium text-gray-700">Deposits</span>
                      <input
                        type="checkbox"
                        checked={setupForm.depositEnabled}
                        onChange={(event) =>
                          setSetupForm((prev) => ({ ...prev, depositEnabled: event.target.checked }))
                        }
                        className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
                      />
                    </label>
                    <label className="flex items-center justify-between gap-3">
                      <span className="font-medium text-gray-700">Withdrawals</span>
                      <input
                        type="checkbox"
                        checked={setupForm.withdrawalEnabled}
                        onChange={(event) =>
                          setSetupForm((prev) => ({ ...prev, withdrawalEnabled: event.target.checked }))
                        }
                        className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
                      />
                    </label>
                  </div>
                  {selectedSetupNetwork && (
                    <div className="mt-4 space-y-2 border-t border-gray-200 pt-4 text-gray-600">
                      <div className="flex justify-between gap-3">
                        <span>Current provider</span>
                        <StatusBadge value={providerLabel(selectedSetupNetwork.provider)} />
                      </div>
                      <div className="flex justify-between gap-3">
                        <span>Flat fee</span>
                        <span>{formatDecimal(selectedSetupNetwork.withdrawalFee)}</span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span>Minimum</span>
                        <span>{formatDecimal(selectedSetupNetwork.minWithdrawal)}</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3 border-t border-gray-200 pt-4">
                <Button
                  type="button"
                  disabled={!hasPermission('wallets:write') || setupMutation.isPending}
                  onClick={() => {
                    setSetupMessage(null);
                    setupMutation.mutate();
                  }}
                >
                  <Save className="h-4 w-4" />
                  Save Provider Setup
                </Button>
                {setupMessage && <span className="text-sm text-emerald-700">{setupMessage}</span>}
              </div>

              {setupMutation.isError && (
                <ErrorPanel message={errorMessage(setupMutation.error, 'Provider setup failed.')} />
              )}
            </>
          )}
        </div>
      </Panel>

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
            <option value="ANIMICA_NODE">Animica node</option>
            <option value="BITCOIN_NODE">Bitcoin-style node</option>
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
                    <td className="px-5 py-4 text-gray-700">{providerLabel(wallet.provider)}</td>
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
                <th className="px-5 py-3">Provider</th>
                <th className="px-5 py-3">Deposits</th>
                <th className="px-5 py-3">Withdrawals</th>
                <th className="px-5 py-3">Min Withdrawal</th>
                <th className="px-5 py-3">Fee</th>
                <th className="px-5 py-3">RPC / Coin</th>
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
                    <StatusBadge value={providerLabel(item.provider)} />
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge value={item.depositEnabled} />
                  </td>
                  <td className="px-5 py-4">
                    <StatusBadge value={item.withdrawalEnabled} />
                  </td>
                  <td className="px-5 py-4 text-gray-700">{formatDecimal(item.minWithdrawal)}</td>
                  <td className="px-5 py-4 text-gray-700">{formatDecimal(item.withdrawalFee)}</td>
                  <td className="max-w-64 truncate px-5 py-4 text-gray-700">
                    {item.provider === 'BITGO' ? item.bitgoCoin ?? 'None' : item.rpcUrl ?? 'None'}
                  </td>
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
