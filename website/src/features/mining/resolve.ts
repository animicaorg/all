import type {
  MiningApiResolution,
  MiningApiResolutionSource,
  MiningEnvHints,
  MiningRequestBase,
} from './types';

const PRIMARY_SITE_HOSTS = new Set(['animica.org', 'www.animica.org']);
const STAGED_SITE_HOSTS = new Set(['dev.animica.org', 'staging.animica.org', 'preview.animica.org']);

export function normalizeAbsoluteUrl(value?: string): string | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;

  try {
    const url = new URL(trimmed);
    return url.toString().replace(/\/+$/, '');
  } catch {
    return undefined;
  }
}

export function joinUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, '');
  const suffix = path.replace(/^\/+/, '');
  return base ? `${base}/${suffix}` : `/${suffix}`;
}

export function buildMiningApiUrl(base: MiningRequestBase, endpointPath: string, currentOrigin?: string): string {
  const cleanPath = endpointPath.replace(/^\/+/, '');

  if (base.kind === 'same-origin') {
    if (!currentOrigin) return `/${cleanPath}`;
    return new URL(`/${cleanPath}`, currentOrigin).toString();
  }

  return joinUrl(base.baseUrl, cleanPath);
}

export function resolveMiningApi(input: {
  currentOrigin?: string;
  currentHostname?: string;
  env?: MiningEnvHints;
}): MiningApiResolution {
  const currentOrigin = normalizeAbsoluteUrl(input.currentOrigin);
  const currentHostname = normalizeHostname(
    input.currentHostname ?? (currentOrigin ? new URL(currentOrigin).hostname : undefined)
  );
  const envMiningApiBaseUrl = normalizeAbsoluteUrl(input.env?.miningApiBaseUrl);
  const envPoolUrl = normalizeAbsoluteUrl(input.env?.poolUrl);

  const diagnostics: string[] = [];
  const sourceDetails = selectMiningApiBase({
    currentOrigin,
    currentHostname,
    envMiningApiBaseUrl,
    envPoolUrl,
  });
  const isLocalDev = isLocalOnlyHost(currentHostname);

  if (sourceDetails.baseUrl) {
    diagnostics.push(`Resolved mining API base via ${sourceDetails.source}: ${sourceDetails.baseUrl}`);
  } else {
    diagnostics.push('No explicit mining API base resolved; same-origin proxy is the only candidate.');
  }

  const requestBases = dedupeRequestBases(
    resolveRequestBases({
      currentOrigin,
      currentHostname,
      isLocalDev,
      baseUrl: sourceDetails.baseUrl,
    })
  );

  if (requestBases.length === 0) {
    requestBases.push({ kind: 'same-origin', label: 'same-origin' });
  }

  return {
    currentOrigin,
    currentHostname,
    source: sourceDetails.source,
    isLocalDev,
    publicBaseUrl: sourceDetails.baseUrl,
    publicPoolHost: sourceDetails.baseUrl ? new URL(sourceDetails.baseUrl).host : undefined,
    requestBases,
    diagnostics,
  };
}

export function normalizeHostname(value?: string): string | undefined {
  if (!value) return undefined;
  return stripPort(value.trim().toLowerCase().replace(/\.$/, '')) || undefined;
}

export function isLocalOnlyHost(value?: string): boolean {
  const hostname = extractHostname(value);
  if (!hostname) return false;

  if (
    hostname === 'localhost' ||
    hostname === '0.0.0.0' ||
    hostname === '::1' ||
    hostname.endsWith('.localhost') ||
    hostname.endsWith('.local')
  ) {
    return true;
  }

  if (/^127\./.test(hostname)) return true;
  if (/^10\./.test(hostname)) return true;
  if (/^192\.168\./.test(hostname)) return true;

  const private172 = hostname.match(/^172\.(\d{1,3})\./);
  if (private172) {
    const secondOctet = Number(private172[1]);
    if (secondOctet >= 16 && secondOctet <= 31) return true;
  }

  return false;
}

export function isLocalOnlyUrl(value?: string): boolean {
  const normalized = normalizeAbsoluteUrl(value);
  if (!normalized) return false;
  return isLocalOnlyHost(new URL(normalized).hostname);
}

function selectMiningApiBase(input: {
  currentOrigin?: string;
  currentHostname?: string;
  envMiningApiBaseUrl?: string;
  envPoolUrl?: string;
}): { source: MiningApiResolutionSource; baseUrl?: string } {
  if (input.envMiningApiBaseUrl) {
    return { source: 'env-mining-api-base', baseUrl: input.envMiningApiBaseUrl };
  }

  if (input.envPoolUrl) {
    return { source: 'env-pool-url', baseUrl: input.envPoolUrl };
  }

  if (input.currentHostname === 'pool.animica.org') {
    return {
      source: 'current-origin-pool',
      baseUrl: input.currentOrigin ?? 'https://pool.animica.org',
    };
  }

  if (input.currentHostname && PRIMARY_SITE_HOSTS.has(input.currentHostname)) {
    return { source: 'production-default', baseUrl: 'https://pool.animica.org' };
  }

  if (input.currentHostname && STAGED_SITE_HOSTS.has(input.currentHostname)) {
    return { source: 'derived-stage-pool', baseUrl: `https://pool.${input.currentHostname}` };
  }

  return { source: 'none' };
}

function resolveRequestBases(input: {
  currentOrigin?: string;
  currentHostname?: string;
  isLocalDev: boolean;
  baseUrl?: string;
}): MiningRequestBase[] {
  const sameOriginBase: MiningRequestBase = { kind: 'same-origin', label: 'same-origin' };
  const baseMatchesCurrentOrigin = Boolean(input.baseUrl && input.currentOrigin && input.baseUrl === input.currentOrigin);

  if (input.currentHostname === 'pool.animica.org') {
    return [sameOriginBase];
  }

  if (input.isLocalDev) {
    return input.baseUrl && !baseMatchesCurrentOrigin
      ? [
          sameOriginBase,
          { kind: 'absolute', label: 'resolved-base', baseUrl: input.baseUrl },
        ]
      : [sameOriginBase];
  }

  if (input.baseUrl && !baseMatchesCurrentOrigin) {
    return [
      { kind: 'absolute', label: 'resolved-base', baseUrl: input.baseUrl },
      sameOriginBase,
    ];
  }

  return [sameOriginBase];
}

function dedupeRequestBases(requestBases: MiningRequestBase[]): MiningRequestBase[] {
  const seen = new Set<string>();
  const deduped: MiningRequestBase[] = [];

  for (const base of requestBases) {
    const key = base.kind === 'same-origin' ? 'same-origin' : `absolute:${base.baseUrl}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(base);
  }

  return deduped;
}

function extractHostname(value?: string): string | undefined {
  if (!value) return undefined;

  const absolute = normalizeAbsoluteUrl(value);
  if (absolute) {
    return normalizeHostname(new URL(absolute).hostname);
  }

  return normalizeHostname(value);
}

function stripPort(value: string): string {
  if (!value) return value;

  if (value.startsWith('[')) {
    const closingBracket = value.indexOf(']');
    if (closingBracket !== -1) {
      return value.slice(1, closingBracket).toLowerCase();
    }
  }

  const colonCount = (value.match(/:/g) ?? []).length;
  if (colonCount === 1) {
    return value.slice(0, value.lastIndexOf(':')).toLowerCase();
  }

  return value.toLowerCase();
}
