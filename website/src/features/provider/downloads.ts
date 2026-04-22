import { existsSync, readFileSync } from 'node:fs';

export type ProviderPlatform = 'windows' | 'linux' | 'python';

export interface ProviderManifestItem {
  platform: ProviderPlatform;
  label: string;
  filename: string;
  version: string;
  size_bytes: number;
  sha256: string;
  url: string;
  notes?: string;
  release_notes?: string;
}

export interface ProviderManifest {
  source?: string;
  version?: string;
  generated_at?: string;
  checksum_file?: string;
  items: ProviderManifestItem[];
}

export interface ProviderDownloadCard {
  platform: ProviderPlatform;
  label: string;
  filename: string;
  version: string;
  sizeLabel: string;
  sha256: string;
  href: string;
  notes?: string;
  releaseNotes?: string;
  installSteps: string[];
  quickStart: string;
}

export interface ProviderDownloadsPageData {
  version: string;
  generatedAt?: string;
  checksumFile?: string;
  cards: ProviderDownloadCard[];
}

const manifestPath = new URL('../../../public/provider/manifest.json', import.meta.url);

function readManifestFile(): ProviderManifest | null {
  if (!existsSync(manifestPath)) {
    return null;
  }

  try {
    return JSON.parse(readFileSync(manifestPath, 'utf-8')) as ProviderManifest;
  } catch {
    return null;
  }
}

function formatBytes(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const rounded = value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1);
  return `${rounded} ${units[index]}`;
}

function installStepsFor(platform: ProviderPlatform): string[] {
  if (platform === 'windows') {
    return [
      'Extract ZIP and open PowerShell in the bundle folder.',
      'Run benchmark first: `./benchmark-worker.bat`.',
      'Generate config: `./aicf-provider-worker.exe init-config --config provider.config.json`.',
      'Start worker: `./start-worker.bat`.',
    ];
  }

  if (platform === 'linux') {
    return [
      'Extract archive: `tar -xzf aicf-provider-worker-<version>-linux-x64.tar.gz`.',
      'Make launchers executable: `chmod +x *.sh`.',
      'Run benchmark: `./benchmark-worker.sh`.',
      'Start daemon: `./start-worker.sh`.',
    ];
  }

  return [
    'Extract Python bundle and create virtual environment.',
    'Install requirements: `pip install -r requirements.txt`.',
    'Benchmark hardware: `python worker.py benchmark --config provider.config.json`.',
    'Start worker: `python worker.py start --config provider.config.json`.',
  ];
}

function quickStartFor(platform: ProviderPlatform): string {
  if (platform === 'windows') {
    return [
      'benchmark-worker.bat',
      'aicf-provider-worker.exe init-config --config provider.config.json',
      'start-worker.bat',
    ].join('\n');
  }

  if (platform === 'linux') {
    return [
      'chmod +x benchmark-worker.sh start-worker.sh',
      './benchmark-worker.sh',
      './start-worker.sh',
    ].join('\n');
  }

  return [
    'python -m venv .venv',
    '. .venv/bin/activate',
    'pip install -r requirements.txt',
    'python worker.py benchmark --config provider.config.json',
    'python worker.py start --config provider.config.json',
  ].join('\n');
}

function fallbackData(): ProviderDownloadsPageData {
  const version = '0.2.0';
  const cards: ProviderDownloadCard[] = [
    {
      platform: 'windows',
      label: 'Windows Worker Bundle',
      filename: `aicf-provider-worker-${version}-windows-x64.zip`,
      version,
      sizeLabel: 'N/A',
      sha256: 'pending',
      href: `/provider/aicf-provider-worker-${version}-windows-x64.zip`,
      notes: 'Includes launcher scripts, config template, logs folder, and executable placeholder.',
      releaseNotes: `/provider/release-notes-${version}.md`,
      installSteps: installStepsFor('windows'),
      quickStart: quickStartFor('windows'),
    },
    {
      platform: 'linux',
      label: 'Linux Worker Bundle',
      filename: `aicf-provider-worker-${version}-linux-x64.tar.gz`,
      version,
      sizeLabel: 'N/A',
      sha256: 'pending',
      href: `/provider/aicf-provider-worker-${version}-linux-x64.tar.gz`,
      notes: 'Includes benchmark/start scripts and systemd unit template.',
      releaseNotes: `/provider/release-notes-${version}.md`,
      installSteps: installStepsFor('linux'),
      quickStart: quickStartFor('linux'),
    },
    {
      platform: 'python',
      label: 'Python Source Bundle',
      filename: `aicf-provider-worker-${version}-python.tar.gz`,
      version,
      sizeLabel: 'N/A',
      sha256: 'pending',
      href: `/provider/aicf-provider-worker-${version}-python.tar.gz`,
      notes: 'Source-first option for custom runtime instrumentation and local modifications.',
      releaseNotes: `/provider/release-notes-${version}.md`,
      installSteps: installStepsFor('python'),
      quickStart: quickStartFor('python'),
    },
  ];

  return {
    version,
    cards,
  };
}

export function loadProviderDownloadsPageData(): ProviderDownloadsPageData {
  const manifest = readManifestFile();
  if (!manifest || !Array.isArray(manifest.items) || manifest.items.length === 0) {
    return fallbackData();
  }

  return {
    version: manifest.version ?? manifest.items[0]?.version ?? 'unknown',
    generatedAt: manifest.generated_at,
    checksumFile: manifest.checksum_file,
    cards: manifest.items.map((item) => ({
      platform: item.platform,
      label: item.label,
      filename: item.filename,
      version: item.version,
      sizeLabel: formatBytes(item.size_bytes),
      sha256: item.sha256,
      href: item.url,
      notes: item.notes,
      releaseNotes: item.release_notes,
      installSteps: installStepsFor(item.platform),
      quickStart: quickStartFor(item.platform),
    })),
  };
}
