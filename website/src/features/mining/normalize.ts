import { isLocalOnlyHost } from './resolve';
import type {
  MiningConfigResponse,
  MiningDownloadItem,
  MiningDownloadsResponse,
  MiningPlatform,
  MiningPoolStatus,
  MiningPoolSummary,
  MiningApiResolution,
  NormalizedMiningConfig,
  NormalizedMiningDownloadItem,
} from './types';

export function normalizeMiningConfig(input: {
  config: MiningConfigResponse;
  resolution: MiningApiResolution;
  statusPayload?: unknown;
  summaryPayload?: unknown;
}): NormalizedMiningConfig {
  const status = mergeMiningStatus(
    unwrapPoolStatus(input.config.status),
    unwrapPoolSummary(input.summaryPayload),
    unwrapPoolStatus(input.statusPayload)
  );

  const warnings = uniqueStrings([
    ...readStringArray(input.config.warnings),
    ...readStringArray(status.warnings),
  ]);

  const reportedStratumUrl = readString(input.config.stratum_url);
  const reportedStratumHost = readString(input.config.stratum_host) ?? readUrlHost(reportedStratumUrl);
  const stratumPort = readNumber(input.config.stratum_port) ?? readUrlPort(reportedStratumUrl);
  const stratumScheme = readString(input.config.stratum_scheme) ?? readUrlScheme(reportedStratumUrl) ?? 'stratum+tcp';
  const publicPoolHost = input.resolution.publicPoolHost;

  let stratumHost = reportedStratumHost ?? publicPoolHost ?? 'Unavailable';
  let stratumUrl =
    reportedStratumUrl ??
    buildStratumUrl({
      scheme: stratumScheme,
      host: stratumHost,
      port: stratumPort,
    });

  if (reportedStratumHost && isLocalOnlyHost(reportedStratumHost) && !input.resolution.isLocalDev) {
    if (publicPoolHost && !isLocalOnlyHost(publicPoolHost)) {
      stratumHost = publicPoolHost;
      stratumUrl = buildStratumUrl({
        scheme: stratumScheme,
        host: publicPoolHost,
        port: stratumPort,
      });
      warnings.unshift(
        `Pool reported local-only stratum host ${reportedStratumHost}; showing public host ${publicPoolHost} instead.`
      );
    } else {
      stratumHost = 'Unavailable';
      stratumUrl = 'Unavailable';
      warnings.unshift('Pool reported a local-only stratum host and no public host could be inferred.');
    }
  } else if (!reportedStratumUrl && stratumHost !== 'Unavailable') {
    stratumUrl = buildStratumUrl({
      scheme: stratumScheme,
      host: stratumHost,
      port: stratumPort,
    });
  } else if (
    reportedStratumUrl &&
    !input.resolution.isLocalDev &&
    isLocalOnlyHost(readUrlHost(reportedStratumUrl)) &&
    publicPoolHost &&
    !isLocalOnlyHost(publicPoolHost)
  ) {
    stratumUrl = buildStratumUrl({
      scheme: stratumScheme,
      host: publicPoolHost,
      port: stratumPort,
    });
  }

  return {
    network: readString(input.config.network) ?? readString(status.network) ?? 'Unknown network',
    chainId: input.config.chain_id ?? status.chain_id,
    poolMode: readString(input.config.pool_mode) ?? readString(status.pool_mode) ?? 'pps',
    poolModeInstructions:
      readString(input.config.pool_mode_instructions) ??
      'Accepted shares and rewards are accounted by the configured pool mode.',
    minerExecutable: readString(input.config.miner_executable) ?? 'animica-miner',
    profile: readString(input.config.profile) ?? 'pool',
    algorithm: readString(input.config.algorithm) ?? 'Unknown',
    deviceType: readString(input.config.device_type) ?? 'miner',
    stratumHost,
    stratumPort,
    stratumScheme,
    stratumUrl,
    payoutInstructions:
      readString(input.config.payout_instructions) ??
      'Use a wallet address that matches the active network shown on this page.',
    workerInstructions:
      readString(input.config.worker_instructions) ??
      'Worker names are labels only. Keep them short and unique for each machine.',
    defaultWorker: readString(input.config.default_worker) ?? 'worker-01',
    defaultThreads: readNumber(input.config.default_threads) ?? 4,
    payoutIntervalSeconds:
      readNumber(input.config.payout_interval_seconds) ??
      readNumber(status.payout_interval_seconds) ??
      0,
    payoutMinAmount:
      readNumber(input.config.payout_min_amount) ??
      readNumber(status.payout_min_amount) ??
      1,
    manualCommands: normalizeManualCommands(input.config.manual_commands),
    status,
    warnings: uniqueStrings(warnings),
    raw: input.config,
  };
}

export function normalizeMiningDownloads(
  payload: MiningDownloadsResponse | MiningDownloadItem[] | undefined,
  resolution: MiningApiResolution
): NormalizedMiningDownloadItem[] {
  const items = unwrapDownloadItems(payload);

  return items.map((item) => {
    const platform = readString(item.platform) ?? 'unknown';
    const normalizedUrl = normalizeDownloadUrl(item.url, platform, resolution);

    return {
      ...item,
      platform,
      label: readString(item.label) ?? defaultPlatformLabel(platform),
      launcher: readString(item.launcher) ?? defaultLauncherLabel(platform),
      entrypoint: readString(item.entrypoint) ?? defaultEntrypoint(platform),
      includesExecutable: readBoolean(item.includes_executable),
      requiresPython: readBoolean(item.requires_python),
      notes: readString(item.notes) ?? 'Download the starter bundle for this platform.',
      normalizedUrl,
    };
  });
}

export function unwrapPoolStatus(payload: unknown): MiningPoolStatus {
  if (isRecord(payload) && isRecord(payload.status)) {
    return normalizePoolPayload(payload.status);
  }

  if (isRecord(payload)) {
    return normalizePoolPayload(payload);
  }

  return {};
}

export function unwrapPoolSummary(payload: unknown): MiningPoolSummary {
  if (isRecord(payload) && isRecord(payload.summary)) {
    return normalizePoolPayload(payload.summary);
  }

  if (isRecord(payload)) {
    return normalizePoolPayload(payload);
  }

  return {};
}

export function mergeMiningStatus(...sources: Array<MiningPoolStatus | MiningPoolSummary | undefined>): MiningPoolStatus {
  const merged: MiningPoolStatus = {};

  for (const source of sources) {
    if (!source) continue;
    for (const [key, value] of Object.entries(source)) {
      if (value !== undefined && value !== null) {
        merged[key] = value;
      }
    }
  }

  return merged;
}

function normalizeManualCommands(
  manualCommands: MiningConfigResponse['manual_commands']
): Partial<Record<MiningPlatform, string>> {
  if (!manualCommands) return {};

  if (Array.isArray(manualCommands)) {
    const firstCommand = manualCommands.find((value) => typeof value === 'string' && value.trim());
    return firstCommand ? { windows: firstCommand, macos: firstCommand, linux: firstCommand } : {};
  }

  const normalized: Partial<Record<MiningPlatform, string>> = {};
  for (const [platform, command] of Object.entries(manualCommands)) {
    if (typeof command === 'string' && command.trim()) {
      normalized[platform as MiningPlatform] = command.trim();
    }
  }
  return normalized;
}

function unwrapDownloadItems(payload: MiningDownloadsResponse | MiningDownloadItem[] | undefined): MiningDownloadItem[] {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.items)) return payload.items;
  return [];
}

function normalizeDownloadUrl(
  rawUrl: string | undefined,
  platform: string,
  resolution: MiningApiResolution
): string | undefined {
  const publicBaseUrl = resolution.publicBaseUrl;
  if (!rawUrl) {
    return publicBaseUrl ? new URL(`api/mining/downloads/${platform}`, ensureTrailingSlash(publicBaseUrl)).toString() : undefined;
  }

  if (!publicBaseUrl) return rawUrl;

  try {
    const resolved = new URL(rawUrl, ensureTrailingSlash(publicBaseUrl));

    if (!resolution.isLocalDev && isLocalOnlyHost(resolved.hostname)) {
      const publicUrl = new URL(ensureTrailingSlash(publicBaseUrl));
      resolved.protocol = publicUrl.protocol;
      resolved.hostname = publicUrl.hostname;
      resolved.port = publicUrl.port;
    }

    return resolved.toString();
  } catch {
    return rawUrl;
  }
}

function buildStratumUrl(input: { scheme: string; host: string | undefined; port: number | undefined }): string {
  if (!input.host || input.host === 'Unavailable') return 'Unavailable';
  if (input.port && Number.isFinite(input.port)) {
    return `${input.scheme}://${input.host}:${input.port}`;
  }
  return `${input.scheme}://${input.host}`;
}

function readUrlHost(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    return new URL(value).hostname;
  } catch {
    return undefined;
  }
}

function readUrlPort(value?: string): number | undefined {
  if (!value) return undefined;
  try {
    const port = new URL(value).port;
    return port ? Number(port) : undefined;
  } catch {
    return undefined;
  }
}

function readUrlScheme(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    return new URL(value).protocol.replace(/:$/, '');
  } catch {
    return undefined;
  }
}

function ensureTrailingSlash(value: string): string {
  return value.endsWith('/') ? value : `${value}/`;
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function readNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0);
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

function defaultPlatformLabel(platform: string): string {
  switch (platform) {
    case 'windows':
      return 'Windows';
    case 'macos':
      return 'macOS';
    case 'linux':
      return 'Ubuntu / Linux';
    default:
      return platform;
  }
}

function defaultLauncherLabel(platform: string): string {
  switch (platform) {
    case 'windows':
      return '.bat launcher';
    case 'macos':
      return '.command launcher';
    case 'linux':
      return '.sh launcher';
    default:
      return 'launcher';
  }
}

function defaultEntrypoint(platform: string): string {
  return platform === 'windows' ? 'animica-miner.exe' : 'animica-miner';
}

function normalizePoolPayload(payload: Record<string, unknown>): MiningPoolStatus {
  const normalized: MiningPoolStatus = { ...payload };

  const chainId = payload.chain_id ?? payload.chainId;
  if (chainId !== undefined && chainId !== null) {
    normalized.chain_id = chainId as string | number;
  }

  const miners = readNumber(payload.miners) ?? readNumber(payload.num_miners);
  if (miners !== undefined) {
    normalized.miners = miners;
  }

  const workers = readNumber(payload.workers) ?? readNumber(payload.num_workers);
  if (workers !== undefined) {
    normalized.workers = workers;
  }

  const latestBlock = readLatestBlock(payload.latest_block);
  if (latestBlock !== undefined) {
    normalized.latest_block = latestBlock;
  } else {
    const lastBlockHash = readString(payload.last_block_hash);
    if (lastBlockHash) normalized.latest_block = lastBlockHash;
  }

  const stratumEndpoint = readString(payload.stratum_endpoint);
  if (stratumEndpoint && !readString(payload.stratum_url)) {
    normalized.stratum_url = stratumEndpoint;
  }

  const payoutIntervalSeconds = readNumber(payload.payout_interval_seconds);
  if (payoutIntervalSeconds !== undefined) {
    normalized.payout_interval_seconds = payoutIntervalSeconds;
  }

  const payoutMinAmount = readNumber(payload.payout_min_amount);
  if (payoutMinAmount !== undefined) {
    normalized.payout_min_amount = payoutMinAmount;
  }

  const payoutCountdownSeconds = readNumber(payload.payout_countdown_seconds);
  if (payoutCountdownSeconds !== undefined) {
    normalized.payout_countdown_seconds = payoutCountdownSeconds;
  }

  const lastPayoutCount = readNumber(payload.last_payout_count);
  if (lastPayoutCount !== undefined) {
    normalized.last_payout_count = lastPayoutCount;
  }

  const nextPayoutAt = readString(payload.next_payout_at);
  if (nextPayoutAt) {
    normalized.next_payout_at = nextPayoutAt;
  }

  const lastPayoutAt = readString(payload.last_payout_at);
  if (lastPayoutAt) {
    normalized.last_payout_at = lastPayoutAt;
  }

  if (typeof payload.payouts_enabled === 'boolean') {
    normalized.payouts_enabled = payload.payouts_enabled;
  }

  if (payload.last_payout_error === null || typeof payload.last_payout_error === 'string') {
    normalized.last_payout_error = payload.last_payout_error;
  }

  return normalized;
}

function readLatestBlock(value: unknown): string | number | undefined {
  const text = readString(value);
  if (text) return text;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (!isRecord(value)) return undefined;

  const hash = readString(value.hash) ?? readString(value.job_id) ?? readString(value.block_hash);
  if (hash) return hash;

  const height = readNumber(value.height);
  if (height !== undefined) return height;

  return undefined;
}

function readBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true' || normalized === '1' || normalized === 'yes') return true;
    if (normalized === 'false' || normalized === '0' || normalized === 'no') return false;
  }
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
