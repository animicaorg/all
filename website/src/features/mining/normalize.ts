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
      notes: readString(item.notes) ?? 'Download the starter bundle for this platform.',
      normalizedUrl,
    };
  });
}

export function unwrapPoolStatus(payload: unknown): MiningPoolStatus {
  if (isRecord(payload) && isRecord(payload.status)) {
    return payload.status as MiningPoolStatus;
  }

  if (isRecord(payload)) {
    return payload as MiningPoolStatus;
  }

  return {};
}

export function unwrapPoolSummary(payload: unknown): MiningPoolSummary {
  if (isRecord(payload) && isRecord(payload.summary)) {
    return payload.summary as MiningPoolSummary;
  }

  if (isRecord(payload)) {
    return payload as MiningPoolSummary;
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

function buildStratumUrl(input: { scheme: string; host?: string; port?: number }): string {
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
