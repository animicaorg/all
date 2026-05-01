import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, PauseCircle, PlayCircle, Search, SlidersHorizontal } from 'lucide-react';
import { apiClient, type Market } from '../services/api';
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
import { errorMessage, formatDateTime, formatDecimal, formatNumber } from '../lib/format';

export default function MarketsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selectedMarket, setSelectedMarket] = useState<Market | null>(null);
  const [reason, setReason] = useState('');
  const [controls, setControls] = useState({
    tradingEnabled: true,
    depositsEnabled: true,
    withdrawalsEnabled: true,
  });

  const params = useMemo(
    () => ({
      page,
      limit: 25,
      query: query || undefined,
      status: status || undefined,
    }),
    [page, query, status]
  );

  const marketsQuery = useQuery({
    queryKey: ['markets', params],
    queryFn: () => apiClient.listMarkets(params),
  });

  useEffect(() => {
    if (!selectedMarket) return;
    setControls({
      tradingEnabled: selectedMarket.marketControl?.tradingEnabled ?? selectedMarket.status === 'ONLINE',
      depositsEnabled: selectedMarket.marketControl?.depositsEnabled ?? true,
      withdrawalsEnabled: selectedMarket.marketControl?.withdrawalsEnabled ?? true,
    });
    setReason(selectedMarket.marketControl?.reason ?? '');
  }, [selectedMarket]);

  const refreshMarkets = async () => {
    await queryClient.invalidateQueries({ queryKey: ['markets'] });
  };

  const statusMutation = useMutation({
    mutationFn: ({ id, nextStatus }: { id: string; nextStatus: Market['status'] }) =>
      apiClient.updateMarketStatus(id, { status: nextStatus, reason: reason || undefined }),
    onSuccess: refreshMarkets,
  });

  const controlsMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.updateMarketControls(id, {
        ...controls,
        reason: reason || null,
      }),
    onSuccess: refreshMarkets,
  });

  const cancelMutation = useMutation({
    mutationFn: (id: string) => apiClient.cancelOpenOrders(id),
    onSuccess: refreshMarkets,
  });

  const markets = marketsQuery.data?.data.markets ?? [];
  const pagination = marketsQuery.data?.data.pagination;

  return (
    <div className="space-y-6">
      <PageHeader title="Markets" description="Trading status, market controls, and open-order intervention." />

      <Panel>
        <div className="grid gap-3 border-b border-gray-200 p-5 md:grid-cols-[1fr_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Market symbol"
              className="h-9 w-full rounded-md border border-gray-300 pl-9 pr-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
            />
          </label>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            className="h-9 rounded-md border border-gray-300 px-3 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
          >
            <option value="">All statuses</option>
            <option value="ONLINE">Online</option>
            <option value="READONLY">Read-only</option>
            <option value="HALTED">Halted</option>
          </select>
          <Button type="button" variant="secondary" onClick={() => marketsQuery.refetch()}>
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>

        {marketsQuery.isLoading ? (
          <div className="p-5">
            <LoadingPanel label="Loading markets" />
          </div>
        ) : marketsQuery.isError ? (
          <div className="p-5">
            <ErrorPanel message={errorMessage(marketsQuery.error, 'Failed to load markets.')} />
          </div>
        ) : markets.length === 0 ? (
          <EmptyState title="No markets found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-3">Market</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Tick</th>
                  <th className="px-5 py-3">Min Size</th>
                  <th className="px-5 py-3">Orders</th>
                  <th className="px-5 py-3">Trades</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {markets.map((market) => (
                  <tr key={market.id} className="cursor-pointer hover:bg-gray-50" onClick={() => setSelectedMarket(market)}>
                    <td className="px-5 py-4">
                      <div className="font-medium text-gray-950">{market.symbol}</div>
                      <div className="text-xs text-gray-500">
                        {market.baseAsset.symbol}/{market.quoteAsset.symbol}
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusBadge value={market.status} />
                    </td>
                    <td className="px-5 py-4 text-gray-600">{formatDecimal(market.priceTick)}</td>
                    <td className="px-5 py-4 text-gray-600">{formatDecimal(market.minOrderSize)}</td>
                    <td className="px-5 py-4 text-gray-600">{formatNumber(market._count?.orders ?? 0)}</td>
                    <td className="px-5 py-4 text-gray-600">{formatNumber(market._count?.trades ?? 0)}</td>
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

      {selectedMarket && (
        <Panel>
          <PanelHeader title="Market Controls" description={selectedMarket.symbol} />
          <div className="grid gap-6 p-5 xl:grid-cols-[1fr_360px]">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Info label="Created" value={formatDateTime(selectedMarket.createdAt)} />
              <Info label="Size Step" value={formatDecimal(selectedMarket.sizeStep)} />
              <Info label="Trading" value={selectedMarket.marketControl?.tradingEnabled ? 'Enabled' : 'Disabled'} />
              <Info label="Reason" value={selectedMarket.marketControl?.reason ?? 'None'} />
            </div>
            <div className="border-t border-gray-200 pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <h3 className="text-sm font-semibold text-gray-950">Controls</h3>
              <div className="mt-4 space-y-3">
                {(['tradingEnabled', 'depositsEnabled', 'withdrawalsEnabled'] as const).map((key) => (
                  <label key={key} className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
                    <span className="capitalize text-gray-700">{key.replace('Enabled', '')}</span>
                    <input
                      type="checkbox"
                      checked={controls[key]}
                      onChange={(event) => setControls((prev) => ({ ...prev, [key]: event.target.checked }))}
                      className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
                    />
                  </label>
                ))}
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={3}
                  placeholder="Operational reason"
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-200"
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={!hasPermission('markets:write') || controlsMutation.isPending}
                    onClick={() => controlsMutation.mutate(selectedMarket.id)}
                  >
                    <SlidersHorizontal className="h-4 w-4" />
                    Save Controls
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!hasPermission('markets:halt') || statusMutation.isPending}
                    onClick={() => statusMutation.mutate({ id: selectedMarket.id, nextStatus: 'ONLINE' })}
                  >
                    <PlayCircle className="h-4 w-4" />
                    Online
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!hasPermission('markets:halt') || statusMutation.isPending}
                    onClick={() => statusMutation.mutate({ id: selectedMarket.id, nextStatus: 'READONLY' })}
                  >
                    <PauseCircle className="h-4 w-4" />
                    Read-only
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!hasPermission('markets:halt') || statusMutation.isPending}
                    onClick={() => statusMutation.mutate({ id: selectedMarket.id, nextStatus: 'HALTED' })}
                  >
                    <Ban className="h-4 w-4" />
                    Halt
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!hasPermission('markets:halt') || cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate(selectedMarket.id)}
                  >
                    Cancel Open Orders
                  </Button>
                </div>
                {(statusMutation.isError || controlsMutation.isError || cancelMutation.isError) && (
                  <ErrorPanel
                    message={errorMessage(
                      statusMutation.error ?? controlsMutation.error ?? cancelMutation.error,
                      'Market action failed.'
                    )}
                  />
                )}
              </div>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 break-words text-sm font-medium text-gray-950">{value}</p>
    </div>
  );
}
