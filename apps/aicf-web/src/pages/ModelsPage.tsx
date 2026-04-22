import { useEffect, useState } from 'react';
import type { ModelDefinition } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';

export function ModelsPage() {
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [message, setMessage] = useState('');

  useEffect(() => {
    aicfApi
      .listModels()
      .then((payload) => setModels(payload.data))
      .catch((error) => setMessage((error as Error).message));
  }, []);

  return (
    <div className="stack">
      <Panel title="Hosted Models" subtitle="Default first-party model lineup available immediately">
        <div className="stats-inline">
          <StatTile label="Chat" value={models.filter((model) => model.type === 'chat').length.toString()} />
          <StatTile label="Embedding" value={models.filter((model) => model.type === 'embedding').length.toString()} />
          <StatTile label="Active" value={models.filter((model) => model.status === 'active').length.toString()} />
        </div>
      </Panel>

      {models.length ? (
        <div className="cards-grid">
          {models.map((model) => (
            <article key={model.id} className="tile">
              <h3>{model.name}</h3>
              <p>
                {model.type} · v{model.version} · {model.defaultProviderRouting}
              </p>
              <ul className="mini-list">
                <li>Base fee: {model.pricing.requestBaseAnmNanos} nanos</li>
                <li>Input token: {model.pricing.inputTokenAnmNanos} nanos</li>
                <li>Output token: {model.pricing.outputTokenAnmNanos} nanos</li>
                <li>Embedding vector: {model.pricing.embeddingVectorAnmNanos} nanos</li>
              </ul>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="No models found" detail="Control plane did not return default model definitions." />
      )}

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
