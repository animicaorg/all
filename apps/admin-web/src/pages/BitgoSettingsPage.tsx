import { useEffect, useState } from 'react';
import { apiClient, type BitgoSettings } from '../services/api';

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

export default function BitgoSettingsPage() {
  const [settings, setSettings] = useState<BitgoSettings>(emptySettings);
  const [accessToken, setAccessToken] = useState('');
  const [webhookSecret, setWebhookSecret] = useState('');
  const [walletsJson, setWalletsJson] = useState('');
  const [coinsJson, setCoinsJson] = useState('');
  const [status, setStatus] = useState<'idle' | 'saving' | 'testing'>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const response = await apiClient.getBitgoSettings();
        setSettings(response.data);
        setWalletsJson(response.data.wallets ? JSON.stringify(response.data.wallets, null, 2) : '');
        setCoinsJson(response.data.coins ? JSON.stringify(response.data.coins, null, 2) : '');
      } catch (err: any) {
        setError(err.response?.data?.message || 'Failed to load BitGo settings.');
      }
    };
    load();
  }, []);

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setStatus('saving');
    setMessage(null);
    setError(null);

    try {
      let wallets = null;
      let coins = null;

      try {
        wallets = walletsJson ? JSON.parse(walletsJson) : null;
        coins = coinsJson ? JSON.parse(coinsJson) : null;
      } catch {
        setError('Wallet IDs or coin settings JSON is invalid.');
        setStatus('idle');
        return;
      }

      const response = await apiClient.updateBitgoSettings({
        environment: settings.environment,
        baseUrl: settings.baseUrl,
        accessToken: accessToken || undefined,
        webhookSecret: webhookSecret || undefined,
        wallets,
        coins,
        enabled: settings.enabled,
      });

      setSettings(response.data);
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
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">BitGo Settings</h1>
        <p className="mt-1 text-sm text-gray-500">
          Configure BitGo environment, credentials, and wallet mappings.
        </p>
      </div>

      {message && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded">
          {message}
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6 bg-white p-6 rounded-lg shadow">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-medium text-gray-900">Connection</h2>
            <p className="text-sm text-gray-500">Enable BitGo and select environment.</p>
          </div>
          <label className="inline-flex items-center">
            <input
              type="checkbox"
              checked={settings.enabled}
              onChange={(e) => setSettings((prev) => ({ ...prev, enabled: e.target.checked }))}
              className="h-4 w-4 text-blue-600 border-gray-300 rounded"
            />
            <span className="ml-2 text-sm text-gray-700">Enabled</span>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700">Environment</label>
            <select
              value={settings.environment}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, environment: e.target.value as 'test' | 'prod' }))
              }
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500"
            >
              <option value="test">Test</option>
              <option value="prod">Production</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">API Base URL (optional)</label>
            <input
              type="url"
              value={settings.baseUrl ?? ''}
              onChange={(e) =>
                setSettings((prev) => ({ ...prev, baseUrl: e.target.value || null }))
              }
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500"
              placeholder="https://app.bitgo-test.com"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700">Access Token</label>
            <input
              type="password"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500"
              placeholder={settings.accessTokenMasked ? settings.accessTokenMasked : 'Not set'}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Webhook Secret</label>
            <input
              type="password"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500"
              placeholder={settings.webhookSecretMasked ? settings.webhookSecretMasked : 'Not set'}
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700">Wallet IDs (JSON)</label>
          <textarea
            value={walletsJson}
            onChange={(e) => setWalletsJson(e.target.value)}
            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 font-mono text-xs"
            rows={4}
            placeholder='{"btc": "wallet-id", "eth": "wallet-id"}'
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700">Coin Settings (JSON)</label>
          <textarea
            value={coinsJson}
            onChange={(e) => setCoinsJson(e.target.value)}
            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 font-mono text-xs"
            rows={4}
            placeholder='{"btc": {"feePolicy": "standard"}}'
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={status !== 'idle'}
            className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {status === 'saving' ? 'Saving...' : 'Save Settings'}
          </button>
          <button
            type="button"
            onClick={handleTest}
            disabled={status !== 'idle'}
            className="px-4 py-2 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 disabled:opacity-50"
          >
            {status === 'testing' ? 'Testing...' : 'Test Connection'}
          </button>
        </div>
      </form>
    </div>
  );
}
