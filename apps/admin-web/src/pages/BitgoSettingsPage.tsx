import { useEffect, useState } from 'react';
import { Plus, Save, Trash2, Wifi } from 'lucide-react';
import { apiClient, type BitgoSettings } from '../services/api';
import { Button, ErrorPanel, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/AdminUI';

const emptySettings: BitgoSettings = {
  id: 'default',
  environment: 'test',
  baseUrl: null,
  wallets: null,
  coins: null,
  enabled: false,
  accessTokenMasked: null,
  webhookSecretMasked: null,
  updatedAt: null,
};

const defaultCoins = ['btc', 'eth', 'sol'];

type WalletMappingRow = {
  coin: string;
  walletId: string;
  feePolicy: string;
};

function objectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function getFeePolicy(value: unknown): string {
  const record = objectRecord(value);
  return typeof record.feePolicy === 'string' ? record.feePolicy : '';
}

function rowsFromSettings(settings: BitgoSettings): WalletMappingRow[] {
  const wallets = settings.wallets ?? {};
  const coins = settings.coins ?? {};
  const coinKeys = Array.from(new Set([...defaultCoins, ...Object.keys(wallets), ...Object.keys(coins)])).sort();

  return coinKeys.map((coin) => ({
    coin,
    walletId: wallets[coin] ?? '',
    feePolicy: getFeePolicy(coins[coin]),
  }));
}

export default function BitgoSettingsPage() {
  const [settings, setSettings] = useState<BitgoSettings>(emptySettings);
  const [accessToken, setAccessToken] = useState('');
  const [webhookSecret, setWebhookSecret] = useState('');
  const [walletRows, setWalletRows] = useState<WalletMappingRow[]>(rowsFromSettings(emptySettings));
  const [status, setStatus] = useState<'idle' | 'saving' | 'testing'>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const response = await apiClient.getBitgoSettings();
        setSettings(response.data);
        setWalletRows(rowsFromSettings(response.data));
      } catch (err: any) {
        setError(err.response?.data?.message || 'Failed to load BitGo settings.');
      }
    };
    load();
  }, []);

  const updateWalletRow = (index: number, patch: Partial<WalletMappingRow>) => {
    setWalletRows((rows) => rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)));
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setStatus('saving');
    setMessage(null);
    setError(null);

    try {
      const wallets: Record<string, string> = {};
      const coins: Record<string, unknown> = { ...(settings.coins ?? {}) };

      for (const row of walletRows) {
        const coin = row.coin.trim().toLowerCase();
        const walletId = row.walletId.trim();
        if (!coin) continue;
        if (walletId) wallets[coin] = walletId;

        if (row.feePolicy.trim()) {
          coins[coin] = {
            ...objectRecord(coins[coin]),
            feePolicy: row.feePolicy.trim(),
          };
        }
      }

      const response = await apiClient.updateBitgoSettings({
        environment: settings.environment,
        baseUrl: settings.baseUrl,
        accessToken: accessToken || undefined,
        webhookSecret: webhookSecret || undefined,
        wallets: Object.keys(wallets).length ? wallets : null,
        coins: Object.keys(coins).length ? coins : null,
        enabled: settings.enabled,
      });

      setSettings(response.data);
      setWalletRows(rowsFromSettings(response.data));
      setAccessToken('');
      setWebhookSecret('');
      setMessage('BitGo settings saved.');
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to save BitGo settings.');
    } finally {
      setStatus('idle');
    }
  };

  const handleTest = async () => {
    setStatus('testing');
    setMessage(null);
    setError(null);
    try {
      const response = await apiClient.testBitgoConnection();
      if (response.data.ok) {
        setMessage(response.data.message);
      } else {
        setError(response.data.message);
      }
    } catch (err: any) {
      setError(err.response?.data?.message || 'BitGo connection test failed.');
    } finally {
      setStatus('idle');
    }
  };

  return (
    <form onSubmit={handleSave} className="max-w-5xl space-y-6">
      <PageHeader
        title="BitGo Settings"
        description="Connection credentials and wallet mappings for BitGo-backed assets."
        actions={<StatusBadge value={settings.enabled ? 'Enabled' : 'Disabled'} />}
      />

      {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      {error && <ErrorPanel message={error} />}

      <Panel>
        <PanelHeader title="Connection" />
        <div className="grid gap-4 p-5 md:grid-cols-2">
          <label className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
            <span className="font-medium text-gray-700">Enabled</span>
            <input
              type="checkbox"
              checked={settings.enabled}
              onChange={(event) => setSettings((prev) => ({ ...prev, enabled: event.target.checked }))}
              className="h-4 w-4 rounded border-gray-300 text-gray-950 focus:ring-gray-400"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">Environment</span>
            <select
              value={settings.environment}
              onChange={(event) =>
                setSettings((prev) => ({ ...prev, environment: event.target.value as 'test' | 'prod' }))
              }
              className="field-input"
            >
              <option value="test">Test</option>
              <option value="prod">Production</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">API base URL</span>
            <input
              type="url"
              value={settings.baseUrl ?? ''}
              onChange={(event) => setSettings((prev) => ({ ...prev, baseUrl: event.target.value || null }))}
              className="field-input"
              placeholder="https://app.bitgo-test.com"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">Access token</span>
            <input
              type="password"
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              className="field-input"
              placeholder={settings.accessTokenMasked ? settings.accessTokenMasked : 'Not set'}
            />
          </label>

          <label className="text-sm md:col-span-2">
            <span className="mb-1 block font-medium text-gray-700">Webhook secret</span>
            <input
              type="password"
              value={webhookSecret}
              onChange={(event) => setWebhookSecret(event.target.value)}
              className="field-input"
              placeholder={settings.webhookSecretMasked ? settings.webhookSecretMasked : 'Not set'}
            />
          </label>
        </div>
      </Panel>

      <Panel>
        <PanelHeader
          title="Wallet Mappings"
          actions={
            <Button
              type="button"
              variant="secondary"
              onClick={() => setWalletRows((rows) => [...rows, { coin: '', walletId: '', feePolicy: '' }])}
            >
              <Plus className="h-4 w-4" />
              Add Coin
            </Button>
          }
        />
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-5 py-3">BitGo coin</th>
                <th className="px-5 py-3">Wallet ID</th>
                <th className="px-5 py-3">Fee policy</th>
                <th className="px-5 py-3">Remove</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {walletRows.map((row, index) => (
                <tr key={`${row.coin}-${index}`}>
                  <td className="px-5 py-3">
                    <input
                      value={row.coin}
                      onChange={(event) => updateWalletRow(index, { coin: event.target.value })}
                      className="field-input min-w-28"
                      placeholder="btc"
                    />
                  </td>
                  <td className="px-5 py-3">
                    <input
                      value={row.walletId}
                      onChange={(event) => updateWalletRow(index, { walletId: event.target.value })}
                      className="field-input min-w-72"
                      placeholder="BitGo wallet ID"
                    />
                  </td>
                  <td className="px-5 py-3">
                    <input
                      value={row.feePolicy}
                      onChange={(event) => updateWalletRow(index, { feePolicy: event.target.value })}
                      className="field-input min-w-44"
                      placeholder="standard"
                    />
                  </td>
                  <td className="px-5 py-3">
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => setWalletRows((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}
                      aria-label={`Remove ${row.coin || 'coin'} mapping`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={status !== 'idle'}>
          <Save className="h-4 w-4" />
          {status === 'saving' ? 'Saving' : 'Save Settings'}
        </Button>
        <Button type="button" variant="secondary" onClick={handleTest} disabled={status !== 'idle'}>
          <Wifi className="h-4 w-4" />
          {status === 'testing' ? 'Testing' : 'Test Connection'}
        </Button>
      </div>
    </form>
  );
}
