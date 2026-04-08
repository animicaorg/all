export type MiningPlatform = 'windows' | 'macos' | 'linux' | (string & {});

export interface MiningPoolStatus {
  online?: boolean;
  network?: string;
  chain_id?: string | number;
  pool_hashrate?: number;
  miners?: number;
  workers?: number;
  height?: number;
  latest_block?: string | number;
  stratum_endpoint?: string;
  warnings?: string[];
  last_update?: string;
  [key: string]: unknown;
}

export interface MiningConfigResponse {
  network?: string;
  chain_id?: string | number;
  pool_enabled?: boolean;
  profile?: string;
  algorithm?: string;
  device_type?: string;
  stratum_host?: string;
  stratum_port?: number;
  stratum_scheme?: string;
  stratum_url?: string;
  api_base_url?: string;
  api_endpoint?: string;
  status_endpoint?: string;
  downloads_endpoint?: string;
  generate_endpoint?: string;
  manual_commands?: Partial<Record<MiningPlatform, string>> | string[];
  default_worker?: string;
  default_threads?: number;
  warnings?: string[];
  payout_instructions?: string;
  worker_instructions?: string;
  status?: MiningPoolStatus;
  [key: string]: unknown;
}

export interface MiningDownloadItem {
  platform: MiningPlatform;
  label?: string;
  filename?: string;
  version?: string;
  launcher?: string;
  sha256?: string;
  size_bytes?: number;
  url?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface MiningDownloadsResponse {
  items?: MiningDownloadItem[];
  [key: string]: unknown;
}

export interface MiningPoolSummary {
  online?: boolean;
  miners?: number;
  workers?: number;
  pool_hashrate?: number;
  height?: number;
  latest_block?: string | number;
  network?: string;
  chain_id?: string | number;
  warnings?: string[];
  last_update?: string;
  [key: string]: unknown;
}

export interface MiningGenerateResponse {
  commands?: Partial<Record<MiningPlatform, string>>;
  config?: {
    filename?: string;
    content?: string;
    [key: string]: unknown;
  };
  starter_files?: Array<{
    platform: MiningPlatform;
    filename: string;
    content: string;
    [key: string]: unknown;
  }>;
  download_urls?: Partial<Record<MiningPlatform, string>>;
  [key: string]: unknown;
}

export interface MiningApiAttempt {
  candidate: string;
  url: string;
  message: string;
  status?: number;
  timedOut?: boolean;
}

export type MiningApiErrorCode =
  | 'http_error'
  | 'network_error'
  | 'timeout'
  | 'invalid_json'
  | 'unavailable';

export interface MiningApiError {
  code: MiningApiErrorCode;
  message: string;
  attempts: MiningApiAttempt[];
}

export type MiningApiResult<T> =
  | {
      ok: true;
      data: T;
      meta: {
        candidate: string;
        url: string;
      };
    }
  | {
      ok: false;
      error: MiningApiError;
    };

export type MiningApiResolutionSource =
  | 'env-mining-api-base'
  | 'env-pool-url'
  | 'production-default'
  | 'current-origin-pool'
  | 'derived-stage-pool'
  | 'none';

export type MiningRequestBase =
  | {
      kind: 'same-origin';
      label: string;
    }
  | {
      kind: 'absolute';
      label: string;
      baseUrl: string;
    };

export interface MiningApiResolution {
  currentOrigin?: string | undefined;
  currentHostname?: string | undefined;
  source: MiningApiResolutionSource;
  isLocalDev: boolean;
  publicBaseUrl?: string | undefined;
  publicPoolHost?: string | undefined;
  requestBases: MiningRequestBase[];
  diagnostics: string[];
}

export interface MiningEnvHints {
  miningApiBaseUrl?: string | undefined;
  poolUrl?: string | undefined;
}

export interface NormalizedMiningDownloadItem extends MiningDownloadItem {
  label: string;
  launcher: string;
  notes: string;
  normalizedUrl?: string | undefined;
}

export interface NormalizedMiningConfig {
  network: string;
  chainId?: string | number | undefined;
  profile: string;
  algorithm: string;
  deviceType: string;
  stratumHost: string;
  stratumPort?: number | undefined;
  stratumScheme: string;
  stratumUrl: string;
  payoutInstructions: string;
  workerInstructions: string;
  defaultWorker: string;
  defaultThreads: number;
  manualCommands: Partial<Record<MiningPlatform, string>>;
  status: MiningPoolStatus;
  warnings: string[];
  raw: MiningConfigResponse;
}
