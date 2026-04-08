import { createMiningApiClient } from './api';
import {
  mergeMiningStatus,
  normalizeMiningConfig,
  normalizeMiningDownloads,
  unwrapPoolStatus,
  unwrapPoolSummary,
} from './normalize';
import { resolveMiningApi } from './resolve';
import type {
  MiningApiError,
  MiningEnvHints,
  MiningPlatform,
  MiningPoolStatus,
  NormalizedMiningConfig,
  NormalizedMiningDownloadItem,
} from './types';

type RuntimeConfig = MiningEnvHints & {
  isDev?: boolean;
};

type PageState = {
  activeTab: MiningPlatform;
  config?: NormalizedMiningConfig;
  downloads: NormalizedMiningDownloadItem[];
  liveStatus: MiningPoolStatus;
  errors: Partial<Record<'config' | 'downloads' | 'status' | 'summary', MiningApiError>>;
  diagnostics: string[];
};

const formatter = new Intl.NumberFormat('en-US');

const DEFAULT_RUNTIME: RuntimeConfig = {
  isDev: false,
};

export async function initMinePage(): Promise<void> {
  const runtime = readRuntimeConfig();
  const resolution = resolveMiningApi({
    currentOrigin: window.location.origin,
    currentHostname: window.location.hostname,
    env: runtime,
  });
  const client = createMiningApiClient({
    resolution,
    currentOrigin: window.location.origin,
  });

  const state: PageState = {
    activeTab: detectPlatform(),
    downloads: [],
    liveStatus: {},
    errors: {},
    diagnostics: [...resolution.diagnostics],
  };

  const elements = resolveElements();
  if (!elements.root) return;

  setActiveTab(elements, state.activeTab);
  renderStaticDefaults(elements, resolution.publicBaseUrl);
  renderDownloads(elements, state.downloads);
  renderGenerated(elements, state);
  setFallback(elements, {
    visible: false,
    message: '',
    directUrl: resolution.publicBaseUrl,
  });

  try {
    const [configResult, downloadsResult, statusResult, summaryResult] = await Promise.all([
      client.fetchConfig(),
      client.fetchDownloads(),
      client.fetchStatus(),
      client.fetchSummary(),
    ]);

    if (statusResult.ok) {
      state.liveStatus = mergeMiningStatus(state.liveStatus, unwrapPoolStatus(statusResult.data));
    } else {
      state.errors.status = statusResult.error;
      state.diagnostics.push(formatDiagnostic('status', statusResult.error));
    }

    if (summaryResult.ok) {
      state.liveStatus = mergeMiningStatus(state.liveStatus, unwrapPoolSummary(summaryResult.data));
    } else {
      state.errors.summary = summaryResult.error;
      state.diagnostics.push(formatDiagnostic('summary', summaryResult.error));
    }

    if (configResult.ok) {
      state.config = normalizeMiningConfig({
        config: configResult.data,
        resolution,
        statusPayload: statusResult.ok ? statusResult.data : undefined,
        summaryPayload: summaryResult.ok ? summaryResult.data : undefined,
      });
      state.liveStatus = mergeMiningStatus(state.liveStatus, state.config.status);
    } else {
      state.errors.config = configResult.error;
      state.diagnostics.push(formatDiagnostic('config', configResult.error));
    }

    if (downloadsResult.ok) {
      state.downloads = normalizeMiningDownloads(downloadsResult.data, resolution);
    } else {
      state.errors.downloads = downloadsResult.error;
      state.diagnostics.push(formatDiagnostic('downloads', downloadsResult.error));
    }
  } catch (error) {
    state.diagnostics.push(error instanceof Error ? error.message : 'Unexpected mine page failure');
  }

  renderState(elements, state, runtime, resolution.publicBaseUrl);
  attachEvents(elements, state);
}

function resolveElements() {
  return {
    root: document.getElementById('mine-page-root'),
    poolStatus: document.getElementById('pool-status-chip'),
    networkChip: document.getElementById('network-chip'),
    profileChip: document.getElementById('profile-chip'),
    warningPanel: document.getElementById('warning-panel'),
    warningList: document.getElementById('warning-list'),
    fallbackPanel: document.getElementById('mining-fallback'),
    fallbackMessage: document.getElementById('mining-fallback-message'),
    fallbackLink: document.getElementById('mining-fallback-link') as HTMLAnchorElement | null,
    debugPanel: document.getElementById('mining-debug'),
    debugOutput: document.getElementById('mining-debug-output'),
    stratumHost: document.getElementById('stratum-host'),
    stratumPort: document.getElementById('stratum-port'),
    stratumUrl: document.getElementById('stratum-url'),
    faqEndpoint: document.getElementById('faq-endpoint'),
    algorithmText: document.getElementById('algorithm-text'),
    deviceType: document.getElementById('device-type'),
    activeMiners: document.getElementById('active-miners'),
    activeWorkers: document.getElementById('active-workers'),
    poolHeight: document.getElementById('pool-height'),
    poolHashrate: document.getElementById('pool-hashrate'),
    latestFoundBlock: document.getElementById('latest-found-block'),
    payoutInstructions: document.getElementById('payout-instructions'),
    workerInstructions: document.getElementById('worker-instructions'),
    payoutAddress: document.getElementById('payout-address') as HTMLInputElement | null,
    workerName: document.getElementById('worker-name') as HTMLInputElement | null,
    threadCount: document.getElementById('thread-count') as HTMLInputElement | null,
    refreshGenerated: document.getElementById('refresh-generated') as HTMLButtonElement | null,
    commandOutput: document.getElementById('command-output'),
    configOutput: document.getElementById('config-output'),
    copyActiveCommand: document.getElementById('copy-active-command') as HTMLButtonElement | null,
    copyConfig: document.getElementById('copy-config') as HTMLButtonElement | null,
    downloadConfig: document.getElementById('download-config') as HTMLButtonElement | null,
    tabButtons: Array.from(document.querySelectorAll<HTMLElement>('.command-tab')),
    downloadCards: Array.from(document.querySelectorAll<HTMLElement>('.download-card')),
  };
}

function renderState(
  elements: ReturnType<typeof resolveElements>,
  state: PageState,
  runtime: RuntimeConfig,
  directPoolUrl?: string
): void {
  renderWarnings(elements, state.config?.warnings ?? []);
  renderStatus(elements, state);
  renderInstructions(elements, state.config);
  renderDownloads(elements, state.downloads);
  renderGenerated(elements, state);
  renderDebug(elements, runtime.isDev === true, state.diagnostics);

  const hasLiveContent = Boolean(state.config) || state.downloads.length > 0 || hasLiveStatus(state.liveStatus);
  const fallbackMessage = buildFallbackMessage(state, directPoolUrl);
  setFallback(elements, {
    visible: Boolean(fallbackMessage) && (!hasLiveContent || Boolean(state.errors.config) || state.downloads.length === 0),
    message: fallbackMessage,
    directUrl: directPoolUrl,
  });
}

function renderStaticDefaults(
  elements: ReturnType<typeof resolveElements>,
  directPoolUrl?: string
): void {
  if (elements.stratumHost) elements.stratumHost.textContent = 'Resolving...';
  if (elements.stratumPort) elements.stratumPort.textContent = 'Resolving...';
  if (elements.stratumUrl) elements.stratumUrl.textContent = 'Loading live pool endpoint...';
  if (elements.faqEndpoint) elements.faqEndpoint.textContent = 'Loading live pool endpoint...';
  if (elements.commandOutput) {
    elements.commandOutput.textContent =
      'Loading live mining configuration...\nThis panel updates when the pool API responds.';
  }
  if (elements.configOutput) {
    elements.configOutput.textContent = '{\n  "status": "loading"\n}';
  }
  if (elements.payoutInstructions) {
    elements.payoutInstructions.textContent =
      'Use an Animica wallet address that matches the active network shown on this page.';
  }
  if (elements.workerInstructions) {
    elements.workerInstructions.textContent =
      'Worker names are labels only. Keep them short and unique per machine.';
  }
  if (elements.fallbackLink && directPoolUrl) {
    elements.fallbackLink.href = directPoolUrl;
  }
}

function renderStatus(elements: ReturnType<typeof resolveElements>, state: PageState): void {
  const config = state.config;
  const liveStatus = state.liveStatus;

  if (elements.poolStatus) {
    const online = liveStatus.online;
    let text = 'Waiting for pool status';
    if (typeof online === 'boolean') {
      text = online ? 'Pool online' : 'Pool offline';
    } else if (state.errors.config || state.errors.status || state.errors.summary) {
      text = 'Live status unavailable';
    }

    elements.poolStatus.textContent = text;
    elements.poolStatus.classList.toggle('bg-emerald-400/10', online === true);
    elements.poolStatus.classList.toggle('border-emerald-400/30', online === true);
    elements.poolStatus.classList.toggle('text-emerald-200', online === true);
    elements.poolStatus.classList.toggle('bg-amber-500/10', online === false);
    elements.poolStatus.classList.toggle('border-amber-400/30', online === false);
    elements.poolStatus.classList.toggle('text-amber-100', online === false);
    elements.poolStatus.classList.toggle('bg-rose-500/10', online === undefined);
    elements.poolStatus.classList.toggle('border-rose-400/30', online === undefined);
    elements.poolStatus.classList.toggle('text-rose-100', online === undefined);
  }

  if (elements.networkChip) {
    elements.networkChip.textContent = config?.network ?? readStatusText(liveStatus.network) ?? 'Unknown network';
  }

  if (elements.profileChip) {
    const profile = config?.profile ?? 'pool';
    const deviceType = config?.deviceType ?? 'miner';
    elements.profileChip.textContent = `${profile} · ${deviceType}`;
  }

  if (elements.algorithmText) {
    elements.algorithmText.textContent = config?.algorithm ?? 'Waiting for config';
  }

  if (elements.deviceType) {
    elements.deviceType.textContent = config?.deviceType ?? 'Waiting for config';
  }

  if (elements.stratumHost) {
    elements.stratumHost.textContent = config?.stratumHost ?? 'Unavailable';
  }

  if (elements.stratumPort) {
    elements.stratumPort.textContent = config?.stratumPort ? String(config.stratumPort) : 'Unavailable';
  }

  if (elements.stratumUrl) {
    elements.stratumUrl.textContent = config?.stratumUrl ?? 'Unavailable';
  }

  if (elements.faqEndpoint) {
    elements.faqEndpoint.textContent = config?.stratumUrl ?? 'Unavailable';
  }

  if (elements.activeMiners) {
    elements.activeMiners.textContent = formatInteger(liveStatus.miners);
  }

  if (elements.activeWorkers) {
    elements.activeWorkers.textContent = formatInteger(liveStatus.workers);
  }

  if (elements.poolHeight) {
    elements.poolHeight.textContent = formatInteger(liveStatus.height);
  }

  if (elements.poolHashrate) {
    elements.poolHashrate.textContent = formatHashrate(liveStatus.pool_hashrate);
  }

  if (elements.latestFoundBlock) {
    elements.latestFoundBlock.textContent = readStatusText(liveStatus.latest_block) ?? 'Unavailable';
  }
}

function renderInstructions(
  elements: ReturnType<typeof resolveElements>,
  config?: NormalizedMiningConfig
): void {
  if (elements.payoutInstructions) {
    elements.payoutInstructions.textContent =
      config?.payoutInstructions ??
      'Use a wallet address that matches the active network shown on this page.';
  }

  if (elements.workerInstructions) {
    elements.workerInstructions.textContent =
      config?.workerInstructions ??
      'Worker names are labels only. Keep them short and unique per machine.';
  }

  if (elements.workerName && config && !elements.workerName.value.trim()) {
    elements.workerName.value = config.defaultWorker;
  }

  if (elements.threadCount && config && !elements.threadCount.value.trim()) {
    const browserThreads = navigator.hardwareConcurrency
      ? Math.max(1, navigator.hardwareConcurrency - 1)
      : config.defaultThreads;
    elements.threadCount.value = String(browserThreads);
  }
}

function renderDownloads(
  elements: ReturnType<typeof resolveElements>,
  downloads: NormalizedMiningDownloadItem[]
): void {
  for (const card of elements.downloadCards) {
    const platform = card.dataset.platform ?? '';
    const item = downloads.find((entry) => entry.platform === platform);
    const link = card.querySelector<HTMLAnchorElement>(`[data-download-link="${platform}"]`);
    const version = card.querySelector<HTMLElement>('[data-download-version]');
    const filename = card.querySelector<HTMLElement>('[data-download-file]');
    const launcher = card.querySelector<HTMLElement>('[data-download-launcher]');
    const size = card.querySelector<HTMLElement>('[data-download-size]');
    const sha = card.querySelector<HTMLElement>('[data-download-sha]');
    const note = card.querySelector<HTMLElement>('[data-download-note]');

    if (!item) {
      if (link) {
        link.href = '#';
        link.setAttribute('aria-disabled', 'true');
        link.classList.add('pointer-events-none', 'opacity-60');
      }
      if (version) version.textContent = 'Unavailable';
      if (filename) filename.textContent = 'Unavailable';
      if (launcher) launcher.textContent = 'Unavailable';
      if (size) size.textContent = 'Unavailable';
      if (sha) sha.textContent = 'Unavailable';
      if (note) note.textContent = 'Download metadata is currently unavailable.';
      continue;
    }

    if (link) {
      if (item.normalizedUrl) {
        link.href = item.normalizedUrl;
        link.setAttribute('aria-disabled', 'false');
        link.classList.remove('pointer-events-none', 'opacity-60');
      } else {
        link.href = '#';
        link.setAttribute('aria-disabled', 'true');
        link.classList.add('pointer-events-none', 'opacity-60');
      }
      link.textContent = `Download ${item.label} bundle`;
    }

    if (version) version.textContent = item.version ?? 'Unversioned';
    if (filename) filename.textContent = item.filename ?? 'Unavailable';
    if (launcher) launcher.textContent = item.launcher;
    if (size) size.textContent = formatBytes(item.size_bytes);
    if (sha) sha.textContent = item.sha256 ?? 'Unavailable';
    if (note) note.textContent = item.notes;
  }
}

function renderGenerated(elements: ReturnType<typeof resolveElements>, state: PageState): void {
  const command = buildCommandSnippet(state);
  const configSnippet = buildConfigSnippet(state);

  if (elements.commandOutput) {
    elements.commandOutput.textContent = command;
  }

  if (elements.configOutput) {
    elements.configOutput.textContent = configSnippet;
  }

  if (elements.downloadConfig) {
    elements.downloadConfig.disabled = !state.config;
  }
}

function renderWarnings(elements: ReturnType<typeof resolveElements>, warnings: string[]): void {
  if (!elements.warningPanel || !elements.warningList) return;

  if (warnings.length === 0) {
    elements.warningPanel.classList.add('hidden');
    elements.warningList.innerHTML = '';
    return;
  }

  elements.warningPanel.classList.remove('hidden');
  elements.warningList.innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('');
}

function renderDebug(
  elements: ReturnType<typeof resolveElements>,
  isDev: boolean,
  diagnostics: string[]
): void {
  if (!elements.debugPanel || !elements.debugOutput) return;

  if (!isDev || diagnostics.length === 0) {
    elements.debugPanel.classList.add('hidden');
    elements.debugOutput.textContent = '';
    return;
  }

  elements.debugPanel.classList.remove('hidden');
  elements.debugOutput.textContent = diagnostics.join('\n');
}

function attachEvents(elements: ReturnType<typeof resolveElements>, state: PageState): void {
  for (const button of elements.tabButtons) {
    button.addEventListener('click', () => {
      const platform = (button.dataset.tabTarget as MiningPlatform | undefined) ?? 'windows';
      state.activeTab = platform;
      setActiveTab(elements, platform);
      renderGenerated(elements, state);
    });
  }

  elements.refreshGenerated?.addEventListener('click', () => renderGenerated(elements, state));
  elements.payoutAddress?.addEventListener('change', () => renderGenerated(elements, state));
  elements.workerName?.addEventListener('change', () => renderGenerated(elements, state));
  elements.threadCount?.addEventListener('change', () => renderGenerated(elements, state));

  document.addEventListener('click', async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;

    if (target.dataset.copyValueTarget) {
      await copyText(readTextContent(target.dataset.copyValueTarget));
    }

    if (target.id === 'copy-active-command' && elements.commandOutput) {
      await copyText(elements.commandOutput.textContent ?? '');
    }

    if (target.id === 'copy-config' && elements.configOutput) {
      await copyText(elements.configOutput.textContent ?? '');
    }

    if (target.dataset.copyDownloadSha) {
      const card = target.closest('.download-card');
      const sha = card?.querySelector('[data-download-sha]')?.textContent ?? '';
      await copyText(sha);
    }

    if (target.id === 'download-config') {
      triggerDownload('animica-miner.config.json', buildConfigSnippet(state));
    }
  });
}

function setActiveTab(elements: ReturnType<typeof resolveElements>, platform: MiningPlatform): void {
  for (const button of elements.tabButtons) {
    const active = button.dataset.tabTarget === platform;
    button.classList.toggle('bg-sky-500/15', active);
    button.classList.toggle('border-sky-300/40', active);
    button.classList.toggle('text-white', active);
    button.classList.toggle('text-slate-300', !active);
  }
}

function setFallback(
  elements: ReturnType<typeof resolveElements>,
  input: { visible: boolean; message: string; directUrl?: string | undefined }
): void {
  if (!elements.fallbackPanel || !elements.fallbackMessage) return;

  elements.fallbackPanel.classList.toggle('hidden', !input.visible);
  elements.fallbackMessage.textContent = input.message;

  if (elements.fallbackLink) {
    if (input.directUrl) {
      elements.fallbackLink.href = input.directUrl;
      elements.fallbackLink.classList.remove('hidden');
    } else {
      elements.fallbackLink.classList.add('hidden');
    }
  }
}

function buildFallbackMessage(state: PageState, directPoolUrl?: string): string {
  if (state.config && state.downloads.length === 0) {
    return 'Live pool configuration loaded, but download metadata is temporarily unavailable.';
  }

  if (!state.config && state.downloads.length > 0) {
    return 'Download metadata loaded, but the live pool configuration could not be reached.';
  }

  if (!state.config && !state.downloads.length) {
    return directPoolUrl
      ? `Live mining data is temporarily unavailable. The pool may still be online at ${directPoolUrl}.`
      : 'Live mining data is temporarily unavailable. Try again shortly or check the pool service directly.';
  }

  return '';
}

function buildCommandSnippet(state: PageState): string {
  const config = state.config;
  if (!config) {
    return 'Live mining commands are unavailable until the pool configuration endpoint responds.';
  }

  const command = config.manualCommands[state.activeTab];
  if (command) return command;

    return [
    '# Reference values',
    `POOL_URL=${config.stratumUrl}`,
    `PAYOUT_ADDRESS=${readPayoutAddress(true)}`,
    `WORKER=${readWorkerName(state, true)}`,
    `THREADS=${readThreadCount(state)}`,
  ].join('\n');
}

function buildConfigSnippet(state: PageState): string {
  return JSON.stringify(
    {
      network: state.config?.network ?? 'unknown',
      algorithm: state.config?.algorithm ?? 'unknown',
      device_type: state.config?.deviceType ?? 'miner',
      stratum_url: state.config?.stratumUrl ?? '',
      payout_address: readPayoutAddress(false),
      worker: readWorkerName(state, false),
      threads: readThreadCount(state),
    },
    null,
    2
  );
}

function readPayoutAddress(placeholder: boolean): string {
  const value = (document.getElementById('payout-address') as HTMLInputElement | null)?.value.trim();
  if (value) return value;
  return placeholder ? '<animica-address>' : '';
}

function readWorkerName(state: PageState, placeholder: boolean): string {
  const value = (document.getElementById('worker-name') as HTMLInputElement | null)?.value.trim();
  if (value) return value;
  if (state.config?.defaultWorker) return state.config.defaultWorker;
  return placeholder ? 'worker-01' : '';
}

function readThreadCount(state: PageState): number {
  const value = (document.getElementById('thread-count') as HTMLInputElement | null)?.value.trim();
  const parsed = value ? Number(value) : Number.NaN;
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  return state.config?.defaultThreads ?? 4;
}

function readRuntimeConfig(): RuntimeConfig {
  const element = document.getElementById('mine-runtime');
  if (!element?.textContent) return DEFAULT_RUNTIME;

  try {
    const parsed = JSON.parse(element.textContent) as RuntimeConfig;
    return { ...DEFAULT_RUNTIME, ...parsed };
  } catch {
    return DEFAULT_RUNTIME;
  }
}

function detectPlatform(): MiningPlatform {
  const ua = navigator.userAgent.toLowerCase();
  if (ua.includes('mac')) return 'macos';
  if (ua.includes('linux')) return 'linux';
  return 'windows';
}

function formatHashrate(value: unknown): string {
  const numeric = typeof value === 'number' ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return '0 H/s';
  if (numeric >= 1_000_000_000) return `${(numeric / 1_000_000_000).toFixed(2)} GH/s`;
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(2)} MH/s`;
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(2)} kH/s`;
  return `${numeric.toFixed(2)} H/s`;
}

function formatBytes(value: unknown): string {
  const numeric = typeof value === 'number' ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return '0 B';
  if (numeric >= 1024 * 1024 * 1024) return `${(numeric / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  if (numeric >= 1024 * 1024) return `${(numeric / (1024 * 1024)).toFixed(2)} MB`;
  if (numeric >= 1024) return `${(numeric / 1024).toFixed(2)} KB`;
  return `${numeric} B`;
}

function formatInteger(value: unknown): string {
  const numeric = typeof value === 'number' ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric) || numeric < 0) return '0';
  return formatter.format(Math.trunc(numeric));
}

function formatDiagnostic(label: string, error: MiningApiError): string {
  const attemptLines = error.attempts.map((attempt) => `- ${attempt.url}: ${attempt.message}`);
  return [`${label}: ${error.message}`, ...attemptLines].join('\n');
}

function readStatusText(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return undefined;
}

function hasLiveStatus(status: MiningPoolStatus): boolean {
  return ['miners', 'workers', 'height', 'pool_hashrate', 'latest_block'].some((key) => status[key] !== undefined);
}

function readTextContent(elementId: string): string {
  return document.getElementById(elementId)?.textContent?.trim() ?? '';
}

async function copyText(value: string): Promise<void> {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    // Ignore clipboard failures in browsers without permission.
  }
}

function triggerDownload(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
