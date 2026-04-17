import { createMiningApiClient } from './api';
import { extractMinerItems, rankMiners, resolveMinerHashrate } from './miners';
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
  MiningMinerItem,
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
  miners: MiningMinerItem[];
  liveStatus: MiningPoolStatus;
  errors: Partial<Record<'config' | 'downloads' | 'status' | 'summary' | 'miners', MiningApiError>>;
  diagnostics: string[];
};

const formatter = new Intl.NumberFormat('en-US');
let payoutCountdownTimer: number | undefined;
let payoutCountdownTargetMs: number | undefined;

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
    miners: [],
    liveStatus: {},
    errors: {},
    diagnostics: [...resolution.diagnostics],
  };

  const elements = resolveElements();
  if (!elements.root) return;

  setActiveTab(elements, state.activeTab);
  renderStaticDefaults(elements, resolution.publicBaseUrl);
  renderDownloads(elements, state.downloads);
  renderMiners(elements, state);
  renderGenerated(elements, state);
  setFallback(elements, {
    visible: false,
    message: '',
    directUrl: resolution.publicBaseUrl,
  });

  try {
    const [configResult, downloadsResult, statusResult, summaryResult, minersResult] = await Promise.all([
      client.fetchConfig(),
      client.fetchDownloads(),
      client.fetchStatus(),
      client.fetchSummary(),
      client.fetchMiners(),
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

    if (minersResult.ok) {
      state.miners = extractMinerItems(minersResult.data);
    } else {
      state.errors.miners = minersResult.error;
      state.diagnostics.push(formatDiagnostic('miners', minersResult.error));
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
    minerSearch: document.getElementById('miner-search') as HTMLInputElement | null,
    minerRows: document.getElementById('miner-rows'),
    minerEmpty: document.getElementById('miner-empty'),
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
    payoutInterval: document.getElementById('payout-interval'),
    payoutCountdown: document.getElementById('payout-countdown'),
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
  renderMiners(elements, state);
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
  if (elements.payoutInterval) elements.payoutInterval.textContent = 'Loading...';
  if (elements.payoutCountdown) elements.payoutCountdown.textContent = 'Loading...';
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
    const mode = (config?.poolMode ?? 'pps').toUpperCase();
    elements.profileChip.textContent = `${profile} · ${deviceType} · ${mode}`;
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
    elements.poolHashrate.textContent = formatHashrate(resolvePoolHashrateForDisplay(liveStatus));
  }

  if (elements.latestFoundBlock) {
    elements.latestFoundBlock.textContent = formatLatestBlock(liveStatus.latest_block);
  }

  const payoutIntervalSeconds =
    readPositiveNumber(liveStatus.payout_interval_seconds) ??
    readPositiveNumber(config?.payoutIntervalSeconds) ??
    0;
  const payoutMinAmount =
    readPositiveNumber(liveStatus.payout_min_amount) ??
    readPositiveNumber(config?.payoutMinAmount) ??
    1;
  const payoutsEnabled =
    typeof liveStatus.payouts_enabled === 'boolean'
      ? liveStatus.payouts_enabled
      : payoutIntervalSeconds > 0;

  if (elements.payoutInterval) {
    if (!payoutsEnabled || payoutIntervalSeconds <= 0) {
      elements.payoutInterval.textContent = 'Disabled';
    } else {
      elements.payoutInterval.textContent = `${formatDuration(payoutIntervalSeconds)} (min ${formatter.format(Math.trunc(payoutMinAmount))})`;
    }
  }

  startPayoutCountdown(elements, {
    enabled: payoutsEnabled && payoutIntervalSeconds > 0,
    nextPayoutAt: liveStatus.next_payout_at,
    countdownSeconds: liveStatus.payout_countdown_seconds,
  });
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
    const workerText =
      config?.workerInstructions ??
      'Worker names are labels only. Keep them short and unique per machine.';
    const modeText = config?.poolModeInstructions ? ` ${config.poolModeInstructions}` : '';
    elements.workerInstructions.textContent = `${workerText}${modeText}`.trim();
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
    const entrypoint = card.querySelector<HTMLElement>('[data-download-entrypoint]');
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
      if (entrypoint) entrypoint.textContent = 'Unavailable';
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
    if (entrypoint) entrypoint.textContent = item.entrypoint;
    if (size) size.textContent = formatBytes(item.size_bytes);
    if (sha) sha.textContent = item.sha256 ?? 'Unavailable';
    if (note) {
      const extra = item.requiresPython
        ? ' Requires Python 3.10+ (no standalone executable in this bundle).'
        : ` Entry point: ${item.entrypoint}.`;
      note.textContent = `${item.notes}${extra}`;
    }
  }
}

function renderMiners(elements: ReturnType<typeof resolveElements>, state: PageState): void {
  if (!elements.minerRows || !elements.minerEmpty) return;

  const query = elements.minerSearch?.value.trim().toLowerCase() ?? '';
  const miners = rankMiners(
    state.miners.filter((item) => {
      if (!query) return true;
      const worker = String(item.worker_name ?? item.worker_id ?? '').toLowerCase();
      const address = String(item.address ?? '').toLowerCase();
      return worker.includes(query) || address.includes(query);
    })
  );

  if (miners.length === 0) {
    elements.minerRows.innerHTML = '';
    elements.minerEmpty.classList.remove('hidden');
    elements.minerEmpty.textContent = query
      ? 'No miners matched your search.'
      : 'No miner activity reported yet.';
    return;
  }

  elements.minerEmpty.classList.add('hidden');
  elements.minerRows.innerHTML = miners
    .map((item) => {
      const worker = escapeHtml(String(item.worker_name ?? item.worker_id ?? 'unknown'));
      const address = escapeHtml(String(item.address ?? ''));
      const sharesAccepted = formatInteger(item.shares_accepted);
      const sharesRejected = formatInteger(item.shares_rejected);
      const blocks = formatInteger(item.blocks_found);
      const hashrate = formatHashrate(resolveMinerHashrate(item));
      const credit = escapeHtml(String(item.credit_total ?? '0'));
      const mode = escapeHtml(String(item.pool_mode ?? state.config?.poolMode ?? 'pps').toUpperCase());
      return `
        <tr class="border-t border-white/10">
          <td class="px-4 py-3 font-medium text-white">${worker}</td>
          <td class="px-4 py-3 text-slate-300">${address || 'n/a'}</td>
          <td class="px-4 py-3 text-slate-300">${sharesAccepted}</td>
          <td class="px-4 py-3 text-slate-300">${sharesRejected}</td>
          <td class="px-4 py-3 text-slate-300">${blocks}</td>
          <td class="px-4 py-3 text-slate-300">${hashrate}</td>
          <td class="px-4 py-3 text-slate-300">${credit}</td>
          <td class="px-4 py-3 text-slate-300">${mode}</td>
        </tr>
      `;
    })
    .join('');
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
  elements.minerSearch?.addEventListener('input', () => renderMiners(elements, state));

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
  if (command) {
    const activeMode = (config.poolMode || 'pps').toLowerCase() === 'solo' ? 'solo' : 'pps';
    const secondaryMode = activeMode === 'solo' ? 'pps' : 'solo';
    const modeExamples = config.raw.mode_examples as
      | Record<string, Partial<Record<MiningPlatform, string>>>
      | undefined;
    const activeCommand = modeExamples?.[activeMode]?.[state.activeTab] ?? command;
    const secondaryCommand = modeExamples?.[secondaryMode]?.[state.activeTab];

    if (secondaryCommand) {
      return [
        `${activeMode.toUpperCase()} mode:`,
        personalizeCommand(activeCommand, state, state.activeTab),
        '',
        `${secondaryMode.toUpperCase()} mode:`,
        personalizeCommand(secondaryCommand, state, state.activeTab),
      ].join('\n');
    }
    return personalizeCommand(activeCommand, state, state.activeTab);
  }

    return [
    '# Reference values',
    `POOL_URL=${config.stratumUrl}`,
    `PAYOUT_ADDRESS=${readPayoutAddress(true)}`,
    `WORKER=${readWorkerName(state, true)}`,
    `THREADS=${readThreadCount(state)}`,
  ].join('\n');
}

function personalizeCommand(command: string, state: PageState, platform: MiningPlatform): string {
  const payout = readPayoutAddress(true);
  const worker = readWorkerName(state, true);
  const threads = readThreadCount(state);
  let resolvedCommand = command;
  const activeDownload = state.downloads.find((item) => item.platform === platform);
  if (activeDownload?.requiresPython) {
    resolvedCommand = resolvedCommand
      .replace(/^animica-miner\.exe\b/, 'py -3 animica_cpu_miner.py')
      .replace(/^\.\/*animica-miner\b/, 'python3 animica_cpu_miner.py');
  }
  return resolvedCommand
    .replace(/--address\s+\S+/g, `--address ${payout}`)
    .replace(/--worker\s+\S+/g, `--worker ${worker}`)
    .replace(/--threads\s+\d+/g, `--threads ${threads}`);
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

function resolvePoolHashrateForDisplay(status: MiningPoolStatus): number {
  const preferredValues: unknown[] = [
    status.hashrate_1m,
    status.pool_hashrate,
    status.hashrate_15m,
    status.hashrate_1h,
    status.hashrate,
    status.hashrate_hps,
    status.hashrate_hsps,
  ];

  for (const value of preferredValues) {
    const parsed = readNonNegativeNumber(value);
    if (parsed !== undefined) return parsed;
  }

  return 0;
}

function readPositiveNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return undefined;
}

function readNonNegativeNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  return undefined;
}

function resolveCountdownTargetMs(
  nextPayoutAt: unknown,
  countdownSeconds: unknown
): number | undefined {
  if (typeof nextPayoutAt === 'string' && nextPayoutAt.trim()) {
    const parsed = Date.parse(nextPayoutAt);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  const seconds = readPositiveNumber(countdownSeconds);
  if (seconds !== undefined) {
    return Date.now() + seconds * 1000;
  }
  return undefined;
}

function formatDuration(secondsRaw: number): string {
  const seconds = Math.max(0, Math.trunc(secondsRaw));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function clearPayoutCountdownTimer(): void {
  if (payoutCountdownTimer !== undefined) {
    window.clearInterval(payoutCountdownTimer);
    payoutCountdownTimer = undefined;
  }
  payoutCountdownTargetMs = undefined;
}

function startPayoutCountdown(
  elements: ReturnType<typeof resolveElements>,
  input: { enabled: boolean; nextPayoutAt?: unknown; countdownSeconds?: unknown }
): void {
  if (!elements.payoutCountdown) return;

  if (!input.enabled) {
    clearPayoutCountdownTimer();
    elements.payoutCountdown.textContent = 'Disabled';
    return;
  }

  const targetMs = resolveCountdownTargetMs(input.nextPayoutAt, input.countdownSeconds);
  if (!targetMs) {
    clearPayoutCountdownTimer();
    elements.payoutCountdown.textContent = 'Pending';
    return;
  }

  payoutCountdownTargetMs = targetMs;
  const update = () => {
    if (!elements.payoutCountdown || payoutCountdownTargetMs === undefined) return;
    const remainingSeconds = Math.max(
      0,
      Math.ceil((payoutCountdownTargetMs - Date.now()) / 1000)
    );
    elements.payoutCountdown.textContent =
      remainingSeconds > 0 ? formatDuration(remainingSeconds) : 'Due now';
  };

  update();
  clearPayoutCountdownTimer();
  payoutCountdownTargetMs = targetMs;
  payoutCountdownTimer = window.setInterval(update, 1000);
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

function formatLatestBlock(value: unknown): string {
  const direct = readStatusText(value);
  if (direct) return direct;

  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const hash =
      readStatusText(record.hash) ??
      readStatusText(record.job_id) ??
      readStatusText(record.block_hash);
    if (hash) return hash;

    const height = readStatusText(record.height);
    if (height) return height;
  }

  return 'Unavailable';
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
