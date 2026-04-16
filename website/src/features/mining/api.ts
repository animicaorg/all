import { HttpError, fetchJSON } from '../../utils/http';
import { buildMiningApiUrl } from './resolve';
import type {
  MiningApiAttempt,
  MiningApiError,
  MiningApiResult,
  MiningApiResolution,
  MiningConfigResponse,
  MiningDownloadsResponse,
  MiningGenerateResponse,
  MiningMinersResponse,
  MiningPoolStatus,
  MiningPoolSummary,
} from './types';

const DEFAULT_TIMEOUT_MS = 6_000;
const MINERS_PAGE_SIZE = 200;
const MAX_MINER_PAGES = 50;

const ENDPOINTS = {
  config: 'api/mining/config',
  downloads: 'api/mining/downloads',
  status: 'api/mining/status',
  summary: 'api/pool/summary',
  miners: 'api/miners',
  generate: 'api/mining/generate',
} as const;

export interface MiningApiClient {
  fetchConfig(): Promise<MiningApiResult<MiningConfigResponse>>;
  fetchDownloads(): Promise<MiningApiResult<MiningDownloadsResponse>>;
  fetchStatus(): Promise<MiningApiResult<MiningPoolStatus>>;
  fetchSummary(): Promise<MiningApiResult<MiningPoolSummary>>;
  fetchMiners(): Promise<MiningApiResult<MiningMinersResponse>>;
  generateStarter(query?: Record<string, string | number | undefined>): Promise<MiningApiResult<MiningGenerateResponse>>;
}

export function createMiningApiClient(input: {
  resolution: MiningApiResolution;
  currentOrigin?: string;
  timeoutMs?: number;
}): MiningApiClient {
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const currentOrigin = input.currentOrigin ?? input.resolution.currentOrigin;

  const request = async <T>(
    endpointPath: string,
    query?: Record<string, string | number | undefined>
  ): Promise<MiningApiResult<T>> => {
    const attempts: MiningApiAttempt[] = [];

    for (const candidate of input.resolution.requestBases) {
      const url = buildMiningApiUrl(candidate, endpointPath, currentOrigin);

      try {
        const data = await fetchJSON<T>(url, {
          method: 'GET',
          timeoutMs,
          retry: { maxRetries: 0, retryOnStatuses: [] },
          ...(query ? { query } : {}),
        });

        return {
          ok: true,
          data,
          meta: {
            candidate: candidate.kind === 'same-origin' ? 'same-origin' : candidate.baseUrl,
            url,
          },
        };
      } catch (error) {
        attempts.push(toAttempt(candidate, url, error));
      }
    }

    return {
      ok: false,
      error: collapseAttempts(attempts),
    };
  };

  return {
    fetchConfig() {
      return request<MiningConfigResponse>(ENDPOINTS.config);
    },
    fetchDownloads() {
      return request<MiningDownloadsResponse>(ENDPOINTS.downloads);
    },
    fetchStatus() {
      return request<MiningPoolStatus>(ENDPOINTS.status);
    },
    fetchSummary() {
      return request<MiningPoolSummary>(ENDPOINTS.summary);
    },
    async fetchMiners() {
      const allItems: NonNullable<MiningMinersResponse['items']> = [];
      let reportedTotal: number | undefined;
      let page = 1;
      let successMeta: { candidate: string; url: string } | undefined;

      while (page <= MAX_MINER_PAGES) {
        const pageResult = await request<MiningMinersResponse>(ENDPOINTS.miners, {
          page,
          page_size: MINERS_PAGE_SIZE,
        });

        if (!pageResult.ok) {
          return pageResult;
        }

        successMeta ??= pageResult.meta;
        const pageItems = Array.isArray(pageResult.data.items) ? pageResult.data.items : [];
        allItems.push(...pageItems);

        const total = toFiniteNumber(pageResult.data.total);
        if (total !== undefined) reportedTotal = total;

        if (pageItems.length === 0) break;
        if (reportedTotal !== undefined && allItems.length >= reportedTotal) break;
        if (pageItems.length < MINERS_PAGE_SIZE) break;

        page += 1;
      }

      const data: MiningMinersResponse =
        reportedTotal !== undefined
          ? { items: allItems, total: reportedTotal }
          : { items: allItems };

      return {
        ok: true,
        data,
        meta: successMeta ?? {
          candidate: 'unknown',
          url: ENDPOINTS.miners,
        },
      };
    },
    generateStarter(query) {
      return request<MiningGenerateResponse>(ENDPOINTS.generate, query);
    },
  };
}

function toAttempt(candidate: MiningApiClientCandidate, url: string, error: unknown): MiningApiAttempt {
  const label = candidate.kind === 'same-origin' ? 'same-origin' : candidate.baseUrl;

  if (error instanceof HttpError) {
    return {
      candidate: label,
      url,
      status: error.status,
      message: error.message,
    };
  }

  if (isTimeoutError(error)) {
    return {
      candidate: label,
      url,
      message: 'Request timed out',
      timedOut: true,
    };
  }

  return {
    candidate: label,
    url,
    message: error instanceof Error ? error.message : 'Unknown fetch failure',
  };
}

function collapseAttempts(attempts: MiningApiAttempt[]): MiningApiError {
  const lastAttempt = attempts.at(-1);

  if (attempts.some((attempt) => attempt.timedOut)) {
    return {
      code: 'timeout',
      message: lastAttempt?.message ?? 'Mining API request timed out',
      attempts,
    };
  }

  if (attempts.some((attempt) => attempt.message.startsWith('Expected JSON'))) {
    return {
      code: 'invalid_json',
      message: lastAttempt?.message ?? 'Mining API returned invalid JSON',
      attempts,
    };
  }

  if (attempts.some((attempt) => typeof attempt.status === 'number')) {
    return {
      code: 'http_error',
      message: lastAttempt?.message ?? 'Mining API returned an HTTP error',
      attempts,
    };
  }

  return {
    code: attempts.length > 0 ? 'network_error' : 'unavailable',
    message: lastAttempt?.message ?? 'Mining API is unavailable',
    attempts,
  };
}

function isTimeoutError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return error.name === 'TimeoutError' || error.message.toLowerCase().includes('timeout');
}

type MiningApiClientCandidate = MiningApiResolution['requestBases'][number];

function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  return undefined;
}
