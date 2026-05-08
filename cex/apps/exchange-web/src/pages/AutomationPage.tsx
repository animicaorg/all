import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Copy, KeyRound, Loader2, Play, Square } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { apiClient } from '../lib/api-client';
import { getApiBaseUrl } from '../lib/endpoints';
import type { ApiKeySummary, StartTradingBotRequest, TradingBotMode } from '../types';
import { Seo } from '../components/Seo';

const botModes: Array<{ mode: TradingBotMode; label: string; detail: string }> = [
  { mode: 'DCA', label: 'DCA', detail: 'Market order on a fixed interval' },
  { mode: 'GRID', label: 'Grid', detail: 'Bid below and ask above the current market' },
  { mode: 'MAKER', label: 'Maker', detail: 'Post-only bid and ask around the spread' },
];

function getErrorMessage(error: any): string {
  return error?.response?.data?.message || error?.response?.data?.error || error?.message || 'Request failed';
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '-';
}

function ScopePill({ scope }: { scope: string }) {
  return (
    <span className="rounded bg-slate-700 px-2 py-1 text-xs font-medium uppercase text-slate-200">
      {scope}
    </span>
  );
}

function ApiKeyRow({ apiKey, onRevoke, isRevoking }: { apiKey: ApiKeySummary; onRevoke: () => void; isRevoking: boolean }) {
  return (
    <tr className="hover:bg-slate-700">
      <td className="px-4 py-3 text-sm text-white">{apiKey.name}</td>
      <td className="px-4 py-3 font-mono text-xs text-slate-300">{apiKey.keyPrefix}...</td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1">
          {apiKey.scopes.map((scope) => <ScopePill key={scope} scope={scope} />)}
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-slate-300">{formatDate(apiKey.lastUsedAt)}</td>
      <td className="px-4 py-3 text-right">
        {apiKey.revokedAt ? (
          <span className="text-sm text-slate-500">Revoked</span>
        ) : (
          <button
            type="button"
            onClick={onRevoke}
            disabled={isRevoking}
            className="rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
          >
            Revoke
          </button>
        )}
      </td>
    </tr>
  );
}

export default function AutomationPage() {
  const queryClient = useQueryClient();
  const [keyName, setKeyName] = useState('Trading key');
  const [keyScopes, setKeyScopes] = useState<string[]>(['read', 'trade']);
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [mode, setMode] = useState<TradingBotMode>('DCA');
  const [market, setMarket] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [intervalSeconds, setIntervalSeconds] = useState('3600');
  const [spacingPct, setSpacingPct] = useState('1');
  const [spreadPct, setSpreadPct] = useState('0.6');
  const [levels, setLevels] = useState('1');

  const apiBaseUrl = getApiBaseUrl();

  const { data: apiKeys = [] } = useQuery({
    queryKey: ['apiKeys'],
    queryFn: () => apiClient.getApiKeys(),
  });

  const { data: markets = [] } = useQuery({
    queryKey: ['markets'],
    queryFn: () => apiClient.getMarkets(),
  });

  const { data: bots = [] } = useQuery({
    queryKey: ['tradingBots'],
    queryFn: () => apiClient.getTradingBots(),
    refetchInterval: 10000,
  });

  const activeBot = useMemo(() => bots.find((bot) => bot.status === 'RUNNING'), [bots]);
  const selectedMarket = market || markets[0]?.symbol || '';

  const createKeyMutation = useMutation({
    mutationFn: () => apiClient.createApiKey(keyName, keyScopes),
    onSuccess: (created) => {
      setNewSecret(created.secret);
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
      toast.success('API key generated');
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const revokeKeyMutation = useMutation({
    mutationFn: (id: string) => apiClient.revokeApiKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
      toast.success('API key revoked');
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const startBotMutation = useMutation({
    mutationFn: (request: StartTradingBotRequest) => apiClient.startTradingBot(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tradingBots'] });
      toast.success('Trading bot started');
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const stopBotMutation = useMutation({
    mutationFn: (id: string) => apiClient.stopTradingBot(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tradingBots'] });
      toast.success('Trading bot stopped');
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const toggleScope = (scope: string) => {
    setKeyScopes((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope]
    );
  };

  const copySecret = async (value: string) => {
    await navigator.clipboard.writeText(value);
    toast.success('Copied');
  };

  const startBot = () => {
    const parsedQuantity = Number(quantity);
    if (!selectedMarket || !Number.isFinite(parsedQuantity) || parsedQuantity <= 0) {
      toast.error('Enter a market and quantity');
      return;
    }

    startBotMutation.mutate({
      mode,
      market: selectedMarket,
      side,
      quantity: parsedQuantity,
      intervalSeconds: Number(intervalSeconds),
      spacingPct: Number(spacingPct),
      spreadPct: Number(spreadPct),
      levels: Number(levels),
    });
  };

  return (
    <div className="space-y-6">
      <Seo
        title="Automation | Animica Exchange"
        description="Manage Animica Exchange API keys and built-in trading bot modes for your account."
        path="/automation"
        noindex
      />

      <h1 className="text-3xl font-bold text-white">Automation</h1>

      <div className="rounded-lg bg-slate-800 p-6">
        <div className="mb-5 flex items-center gap-2">
          <KeyRound size={20} className="text-blue-300" />
          <h2 className="text-lg font-semibold text-white">API Keys</h2>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-300">Name</span>
              <input
                value={keyName}
                onChange={(event) => setKeyName(event.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
              />
            </label>
            <div>
              <div className="mb-2 text-sm font-medium text-slate-300">Scopes</div>
              <div className="flex gap-2">
                {['read', 'trade'].map((scope) => (
                  <button
                    key={scope}
                    type="button"
                    onClick={() => toggleScope(scope)}
                    className={`rounded-md px-3 py-2 text-sm font-medium uppercase ${
                      keyScopes.includes(scope)
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                    }`}
                  >
                    {scope}
                  </button>
                ))}
              </div>
            </div>
            <button
              type="button"
              onClick={() => createKeyMutation.mutate()}
              disabled={createKeyMutation.isPending || !keyName.trim()}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
            >
              {createKeyMutation.isPending && <Loader2 className="animate-spin" size={16} />}
              Generate Key
            </button>
          </div>

          <div className="overflow-x-auto rounded-md border border-slate-700">
            <table className="w-full">
              <thead className="bg-slate-700">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-300">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-300">Key</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-300">Scopes</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-slate-300">Last Used</th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-slate-300">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {apiKeys.map((apiKey) => (
                  <ApiKeyRow
                    key={apiKey.id}
                    apiKey={apiKey}
                    onRevoke={() => revokeKeyMutation.mutate(apiKey.id)}
                    isRevoking={revokeKeyMutation.isPending}
                  />
                ))}
                {apiKeys.length === 0 && (
                  <tr>
                    <td className="px-4 py-6 text-sm text-slate-400" colSpan={5}>No API keys</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {newSecret && (
          <div className="mt-5 rounded-md border border-amber-500/40 bg-amber-500/10 p-4">
            <div className="mb-2 text-sm font-semibold text-amber-200">New key secret</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-slate-950 px-3 py-2 text-xs text-white">{newSecret}</code>
              <button
                type="button"
                onClick={() => copySecret(newSecret)}
                className="rounded-md bg-slate-700 p-2 text-white hover:bg-slate-600"
                title="Copy"
              >
                <Copy size={16} />
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-lg bg-slate-800 p-6">
        <div className="mb-5 flex items-center gap-2">
          <Bot size={20} className="text-green-300" />
          <h2 className="text-lg font-semibold text-white">Trading Bot</h2>
        </div>

        {activeBot && (
          <div className="mb-5 flex flex-col gap-3 rounded-md border border-green-500/30 bg-green-500/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-green-100">
              Running {activeBot.mode} on {activeBot.market}
              {activeBot.nextRunAt && <span className="text-green-300"> · next {formatDate(activeBot.nextRunAt)}</span>}
              {activeBot.lastError && <span className="block text-red-300">{activeBot.lastError}</span>}
            </div>
            <button
              type="button"
              onClick={() => stopBotMutation.mutate(activeBot.id)}
              disabled={stopBotMutation.isPending}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
            >
              <Square size={14} />
              Stop
            </button>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          {botModes.map((item) => (
            <button
              key={item.mode}
              type="button"
              onClick={() => setMode(item.mode)}
              className={`rounded-md border p-4 text-left ${
                mode === item.mode
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-slate-700 bg-slate-900 hover:bg-slate-700'
              }`}
            >
              <div className="font-semibold text-white">{item.label}</div>
              <div className="mt-1 text-sm text-slate-400">{item.detail}</div>
            </button>
          ))}
        </div>

        <div className="mt-5 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-300">Market</span>
            <select
              value={selectedMarket}
              onChange={(event) => setMarket(event.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
            >
              {markets.map((item) => (
                <option key={item.symbol} value={item.symbol}>{item.symbol}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-300">Side</span>
            <select
              value={side}
              onChange={(event) => setSide(event.target.value as 'buy' | 'sell')}
              disabled={mode !== 'DCA'}
              className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500 disabled:opacity-50"
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </label>

          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-300">Quantity</span>
            <input
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              inputMode="decimal"
              placeholder="0.00"
              className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
            />
          </label>

          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-300">Interval Seconds</span>
            <input
              value={intervalSeconds}
              onChange={(event) => setIntervalSeconds(event.target.value)}
              inputMode="numeric"
              className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
            />
          </label>

          {mode === 'GRID' && (
            <>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-300">Spacing %</span>
                <input
                  value={spacingPct}
                  onChange={(event) => setSpacingPct(event.target.value)}
                  inputMode="decimal"
                  className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-slate-300">Levels</span>
                <input
                  value={levels}
                  onChange={(event) => setLevels(event.target.value)}
                  inputMode="numeric"
                  className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
                />
              </label>
            </>
          )}

          {mode === 'MAKER' && (
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-slate-300">Spread %</span>
              <input
                value={spreadPct}
                onChange={(event) => setSpreadPct(event.target.value)}
                inputMode="decimal"
                className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none focus:border-blue-500"
              />
            </label>
          )}
        </div>

        <button
          type="button"
          onClick={startBot}
          disabled={startBotMutation.isPending || !selectedMarket}
          className="mt-5 inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-3 text-sm font-semibold text-white hover:bg-green-500 disabled:opacity-50"
        >
          {startBotMutation.isPending ? <Loader2 className="animate-spin" size={16} /> : <Play size={16} />}
          Start Bot
        </button>
      </div>

      <div className="rounded-lg bg-slate-800 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">API Key Docs</h2>
        <div className="space-y-4 text-sm text-slate-300">
          <p>Use `Authorization: Bearer YOUR_KEY` or `X-API-Key: YOUR_KEY`. `read` can access balances, orders, and trades. `trade` can place and cancel orders.</p>
          <pre className="overflow-x-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">{`curl -H "Authorization: Bearer anm_live_..." \\
  ${apiBaseUrl}/me/balances`}</pre>
          <pre className="overflow-x-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">{`curl -X POST ${apiBaseUrl}/orders \\
  -H "Authorization: Bearer anm_live_..." \\
  -H "Content-Type: application/json" \\
  -d '{"symbol":"LTC-ANM","side":"buy","type":"LIMIT","price":1,"quantity":0.01}'`}</pre>
        </div>
      </div>
    </div>
  );
}
