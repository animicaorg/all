import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, PauseCircle, PlayCircle, Plus, Search, SlidersHorizontal } from 'lucide-react';
import { apiClient, type Market, type MarketAssetOption } from '../services/api';
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

const defaultMarketForm = {
  symbol: '',
  baseAsset: '',
  quoteAsset: 'USDT',
  priceTick: '0.01',
  sizeStep: '0.001',
  minOrderSize: '0.001',
  makerFeeBps: '10',
  takerFeeBps: '20',
  feeAsset: 'USDT',
  status: 'ONLINE' as Market['status'],
};

type MarketFormState = typeof defaultMarketForm;

function preferredBaseAsset(assets: MarketAssetOption[]): string {
  const symbols = new Set(assets.map((asset) => asset.symbol));
  if (symbols.has('ANM')) return 'ANM';
  return assets.find((asset) => !['USDT', 'USDC'].includes(asset.symbol))?.symbol ?? assets[0]?.symbol ?? '';
}

function preferredQuoteAsset(assets: MarketAssetOption[], baseAsset: string): string {
  const symbols = new Set(assets.map((asset) => asset.symbol));
  if (symbols.has('USDT') && baseAsset !== 'USDT') return 'USDT';
  if (symbols.has('USDC') && baseAsset !== 'USDC') return 'USDC';
  return assets.find((asset) => asset.symbol !== baseAsset)?.symbol ?? '';
}

function withAssetDefaults(form: MarketFormState, assets: MarketAssetOption[]): MarketFormState {
  if (assets.length === 0) return form;

  const symbols = new Set(assets.map((asset) => asset.symbol));
  const baseAsset = symbols.has(form.baseAsset) ? form.baseAsset : preferredBaseAsset(assets);
  const quoteAsset =
    symbols.has(form.quoteAsset) && form.quoteAsset !== baseAsset
      ? form.quoteAsset
      : preferredQuoteAsset(assets, baseAsset);
  const feeAsset = symbols.has(form.feeAsset) ? form.feeAsset : quoteAsset || baseAsset;

  if (baseAsset === form.baseAsset && quoteAsset === form.quoteAsset && feeAsset === form.feeAsset) return form;
  return { ...form, baseAsset, quoteAsset, feeAsset };
}

function assetLabel(asset: MarketAssetOption): string {
  const parts = [asset.name !== asset.symbol ? asset.name : '', asset.sources.join('/')]
    .filter(Boolean)
    .join(' - ');
  return parts ? `${asset.symbol} - ${parts}` : asset.symbol;
}

export default function MarketsPage() {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selectedMarket, setSelectedMarket] = useState<Market | null>(null);
  const [reason, setReason] = useState('');
  const [marketForm, setMarketForm] = useState(defaultMarketForm);
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

  const assetsQuery = useQuery({
    queryKey: ['market-assets'],
    queryFn: () => apiClient.listMarketAssets(),
  });

  const assetOptions = useMemo(
    () => (assetsQuery.data?.data.assets ?? []).filter((asset) => asset.enabled),
    [assetsQuery.data]
  );

  useEffect(() => {
    if (!selectedMarket) return;
    setControls({
      tradingEnabled: selectedMarket.marketControl?.tradingEnabled ?? selectedMarket.status === 'ONLINE',
      depositsEnabled: selectedMarket.marketControl?.depositsEnabled ?? true,
      withdrawalsEnabled: selectedMarket.marketControl?.withdrawalsEnabled ?? true,
    });
    setReason(selectedMarket.marketControl?.reason ?? '');
  }, [selectedMarket]);

  useEffect(() => {
    setMarketForm((prev) => withAssetDefaults(prev, assetOptions));
  }, [assetOptions]);

  const refreshMarkets = async () => {
    await queryClient.invalidateQueries({ queryKey: ['markets'] });
  };

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient.createMarket({
        ...marketForm,
        baseAsset: marketForm.baseAsset.trim().toUpperCase(),
        quoteAsset: marketForm.quoteAsset.trim().toUpperCase(),
        symbol: marketForm.symbol.trim() ? marketForm.symbol.trim().toUpperCase() : undefined,
        feeAsset: marketForm.feeAsset.trim() ? marketForm.feeAsset.trim().toUpperCase() : undefined,
      }),
    onSuccess: async (response) => {
      setMarketForm(withAssetDefaults(defaultMarketForm, assetOptions));
      setSelectedMarket(response.data.market);
      await refreshMarkets();
    },
  });

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
  const hasAssetOptions = assetOptions.length > 0;
  const createDisabled =
    !hasPermission('markets:write') ||
    createMutation.isPending ||
    assetsQuery.isLoading ||
    !hasAssetOptions ||
    !marketForm.baseAsset ||
    !marketForm.quoteAsset ||
    marketForm.baseAsset === marketForm.quoteAsset;
  const tradingEnabled = selectedMarket
    ? (selectedMarket.marketControl?.tradingEnabled ?? selectedMarket.status === 'ONLINE')
    : false;

  return (
    <div className="space-y-6">
      <PageHeader title="Markets" description="Trading status, market controls, and open-order intervention." />

      <Panel>
        <PanelHeader title="Create Trading Pair" />
        <form
          className="grid gap-4 p-5 lg:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate();
          }}
        >
          <Field label="Base Asset">
            <select
              value={marketForm.baseAsset}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, baseAsset: event.target.value }))}
              className="field-input"
              disabled={assetsQuery.isLoading || !hasAssetOptions}
              required
            >
              <option value="" disabled>
                {assetsQuery.isLoading ? 'Loading assets' : 'Select asset'}
              </option>
              {assetOptions.map((asset) => (
                <option key={asset.symbol} value={asset.symbol} disabled={asset.symbol === marketForm.quoteAsset}>
                  {assetLabel(asset)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Quote Asset">
            <select
              value={marketForm.quoteAsset}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, quoteAsset: event.target.value }))}
              className="field-input"
              disabled={assetsQuery.isLoading || !hasAssetOptions}
              required
            >
              <option value="" disabled>
                {assetsQuery.isLoading ? 'Loading assets' : 'Select asset'}
              </option>
              {assetOptions.map((asset) => (
                <option key={asset.symbol} value={asset.symbol} disabled={asset.symbol === marketForm.baseAsset}>
                  {assetLabel(asset)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Symbol">
            <input
              value={marketForm.symbol}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, symbol: event.target.value }))}
              placeholder="BTC-USDT"
              className="field-input uppercase"
            />
          </Field>
          <Field label="Initial Status">
            <select
              value={marketForm.status}
              onChange={(event) =>
                setMarketForm((prev) => ({ ...prev, status: event.target.value as Market['status'] }))
              }
              className="field-input"
            >
              <option value="ONLINE">Online</option>
              <option value="HALTED">Halted</option>
            </select>
          </Field>
          <Field label="Price Tick">
            <input
              value={marketForm.priceTick}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, priceTick: event.target.value }))}
              inputMode="decimal"
              className="field-input"
              required
            />
          </Field>
          <Field label="Size Step">
            <input
              value={marketForm.sizeStep}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, sizeStep: event.target.value }))}
              inputMode="decimal"
              className="field-input"
              required
            />
          </Field>
          <Field label="Min Order Size">
            <input
              value={marketForm.minOrderSize}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, minOrderSize: event.target.value }))}
              inputMode="decimal"
              className="field-input"
              required
            />
          </Field>
          <Field label="Fee Asset">
            <select
              value={marketForm.feeAsset}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, feeAsset: event.target.value }))}
              className="field-input"
              disabled={assetsQuery.isLoading || !hasAssetOptions}
              required
            >
              <option value="" disabled>
                {assetsQuery.isLoading ? 'Loading assets' : 'Select asset'}
              </option>
              {assetOptions.map((asset) => (
                <option key={asset.symbol} value={asset.symbol}>
                  {assetLabel(asset)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Maker Bps">
            <input
              value={marketForm.makerFeeBps}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, makerFeeBps: event.target.value }))}
              inputMode="numeric"
              className="field-input"
              required
            />
          </Field>
          <Field label="Taker Bps">
            <input
              value={marketForm.takerFeeBps}
              onChange={(event) => setMarketForm((prev) => ({ ...prev, takerFeeBps: event.target.value }))}
              inputMode="numeric"
              className="field-input"
              required
            />
          </Field>
          <div className="flex items-end lg:col-span-2">
            <Button type="submit" disabled={createDisabled}>
              <Plus className="h-4 w-4" />
              Create Pair
            </Button>
          </div>
          {assetsQuery.isError && (
            <div className="lg:col-span-4">
              <ErrorPanel message={errorMessage(assetsQuery.error, 'Failed to load market assets.')} />
            </div>
          )}
          {createMutation.isError && (
            <div className="lg:col-span-4">
              <ErrorPanel message={errorMessage(createMutation.error, 'Market creation failed.')} />
            </div>
          )}
        </form>
      </Panel>

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
                  <th className="px-5 py-3">Fees</th>
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
                    <td className="px-5 py-4 text-gray-600">
                      {market.makerFeeBps}/{market.takerFeeBps} bps
                    </td>
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
              <Info label="Trading" value={tradingEnabled ? 'Enabled' : 'Disabled'} />
              <Info label="Fees" value={`${selectedMarket.makerFeeBps}/${selectedMarket.takerFeeBps} bps`} />
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="text-sm">
      <span className="mb-1 block font-medium text-gray-700">{label}</span>
      {children}
    </label>
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
