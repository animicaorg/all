import { useEffect, useMemo, useState } from 'react';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';

type ProviderArtifact = {
  platform: 'windows' | 'linux' | 'python';
  label: string;
  filename: string;
  version: string;
  sizeBytes: number;
  sha256: string;
  url: string;
  notes?: string;
  releaseNotes?: string;
};

const fallbackArtifacts: ProviderArtifact[] = [
  {
    platform: 'windows',
    label: 'Windows GPU Worker Executable Bundle',
    filename: 'aicf-provider-worker-0.2.0-windows-x64.zip',
    version: '0.2.0',
    sizeBytes: 0,
    sha256: 'pending',
    url: '/provider/aicf-provider-worker-0.2.0-windows-x64.zip',
    notes: 'Includes config template, benchmark/start launchers, and executable placeholder.'
  },
  {
    platform: 'linux',
    label: 'Linux GPU Worker Bundle',
    filename: 'aicf-provider-worker-0.2.0-linux-x64.tar.gz',
    version: '0.2.0',
    sizeBytes: 0,
    sha256: 'pending',
    url: '/provider/aicf-provider-worker-0.2.0-linux-x64.tar.gz',
    notes: 'Includes benchmark/start scripts and systemd unit template.'
  },
  {
    platform: 'python',
    label: 'Python Source Bundle',
    filename: 'aicf-provider-worker-0.2.0-python.tar.gz',
    version: '0.2.0',
    sizeBytes: 0,
    sha256: 'pending',
    url: '/provider/aicf-provider-worker-0.2.0-python.tar.gz',
    notes: 'Source-first worker package with venv quickstart.'
  }
];

function formatBytes(size: number) {
  if (!size) return 'n/a';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const rounded = value >= 10 || idx === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${rounded} ${units[idx]}`;
}

export function MiningContributePage() {
  const [artifacts, setArtifacts] = useState<ProviderArtifact[]>([]);
  const [manifestVersion, setManifestVersion] = useState('0.2.0');
  const [message, setMessage] = useState('');

  useEffect(() => {
    aicfApi
      .listProviderDownloads()
      .then((payload) => {
        const normalized = payload.manifest.items.map((item) => ({
          platform: item.platform,
          label: item.label,
          filename: item.filename,
          version: item.version,
          sizeBytes: item.size_bytes,
          sha256: item.sha256,
          url: item.url,
          notes: item.notes,
          releaseNotes: item.release_notes,
        }));

        if (normalized.length > 0) {
          setArtifacts(normalized);
          setManifestVersion(payload.manifest.version ?? normalized[0].version);
          return;
        }

        setMessage('Provider download manifest is empty. Using fallback release list.');
      })
      .catch((error) => {
        setMessage(`Provider bundle manifest unavailable: ${(error as Error).message}. Using fallback release list.`);
      });
  }, []);

  const resolved = useMemo(() => (artifacts.length ? artifacts : fallbackArtifacts), [artifacts]);

  return (
    <div className="stack">
      <Panel title="Contribute GPU Compute" subtitle="Install AICF provider worker bundles and earn ANM from useful workloads.">
        <p className="muted">Current release channel: {manifestVersion}</p>
        {message ? <p className="muted">{message}</p> : null}
        <div className="cards-grid">
          {resolved.map((artifact) => (
            <article key={artifact.filename} className="tile">
              <h3>{artifact.label}</h3>
              <p className="muted">{artifact.notes}</p>
              <p className="muted">
                <code>{artifact.filename}</code>
              </p>
              <p className="muted">Version: {artifact.version}</p>
              <p className="muted">Size: {formatBytes(artifact.sizeBytes)}</p>
              <p className="muted" style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.7rem', wordBreak: 'break-all' }}>
                SHA256: {artifact.sha256}
              </p>
              <div className="row">
                <a className="ghost" href={artifact.url}>
                  Download
                </a>
                {artifact.releaseNotes ? (
                  <a className="ghost" href={artifact.releaseNotes}>
                    Release notes
                  </a>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      </Panel>

      <Panel title="Provider Quickstart" subtitle="Recommended onboarding flow for GPU providers">
        <ol className="mini-list">
          <li>1. Download worker bundle for your platform.</li>
          <li>2. Generate and edit provider config with wallet + provider credentials.</li>
          <li>3. Run benchmark mode and verify hardware detection.</li>
          <li>4. Start worker daemon and confirm heartbeat status.</li>
          <li>5. Track jobs, utilization, and rewards on provider dashboard routes.</li>
        </ol>
        <pre>{`# Linux quickstart
tar -xzf aicf-provider-worker-0.2.0-linux-x64.tar.gz
cd aicf-provider-worker
cp provider.config.example.json provider.config.json
./benchmark-worker.sh
./start-worker.sh`}</pre>
      </Panel>

      <Panel title="Commands" subtitle="Benchmark, config generation, health, and startup">
        <pre>{`aicf-provider-worker init-config --config provider.config.json
aicf-provider-worker benchmark --config provider.config.json
aicf-provider-worker health --config provider.config.json
aicf-provider-worker start --config provider.config.json`}</pre>
      </Panel>
    </div>
  );
}
