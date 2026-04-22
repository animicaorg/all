import { useEffect, useState } from 'react';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

const DEFAULT_SCOPES = ['inference:chat', 'inference:embeddings', 'jobs:write', 'jobs:read'];

export function ApiKeysPage() {
  const { session } = useSession();
  const [keys, setKeys] = useState<Array<{ id: string; name: string; prefix: string; scopes: string[]; revokedAt?: string }>>([]);
  const [name, setName] = useState('default-key');
  const [latestToken, setLatestToken] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    if (!session?.selectedProjectId) return;
    const payload = await aicfApi.listApiKeys(session, session.selectedProjectId);
    setKeys(payload.apiKeys);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, session?.selectedProjectId]);

  async function createKey() {
    if (!session?.selectedProjectId) {
      setMessage('Select a project first');
      return;
    }
    try {
      const payload = await aicfApi.createApiKey(session, session.selectedProjectId, {
        name,
        scopes: DEFAULT_SCOPES
      });
      setLatestToken(payload.token);
      setMessage(`Created key ${payload.prefix}`);
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="API Keys" subtitle="Create scoped credentials for OpenAI-compatible model endpoints">
        <label>
          Key name
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <button onClick={createKey}>Create API Key</button>
        <p className="muted">Scopes: {DEFAULT_SCOPES.join(', ')}</p>
        {latestToken ? (
          <pre>
            Store now (shown once):
            {'\n'}
            {latestToken}
          </pre>
        ) : null}
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {keys.length ? (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Scopes</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id}>
                <td>{key.name}</td>
                <td>{key.prefix}</td>
                <td>{key.scopes.join(', ')}</td>
                <td>{key.revokedAt ? 'revoked' : 'active'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No API keys" detail="Issue a key to call /v1/chat/completions and /v1/embeddings." />
      )}
    </div>
  );
}
