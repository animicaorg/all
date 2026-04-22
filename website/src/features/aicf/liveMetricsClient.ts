export type AicfStatusPayload = {
  status?: string;
  timestamp?: string;
  paused?: boolean;
  health?: {
    ok?: boolean;
    counts?: Record<string, unknown>;
  };
};

export type AicfCounts = {
  users: number;
  projects: number;
  providers: number;
  nodes: number;
  jobs: number;
  contractJobs: number;
  agentTasks: number;
  usage: number;
  settlements: number;
  queueUnits: number;
};

function normalizeBaseUrl(value: string): string {
  const trimmed = String(value || '').trim();
  if (!trimmed) return '/api';
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
}

function asCount(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.trunc(n);
}

export async function fetchAicfStatus(baseUrl: string, timeoutMs = 7000): Promise<{
  payload: AicfStatusPayload;
  latencyMs: number;
}> {
  const normalizedBase = normalizeBaseUrl(baseUrl);
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), Math.max(500, timeoutMs));
  const started = performance.now();

  try {
    const response = await fetch(`${normalizedBase}/status`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
      cache: 'no-store',
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = (await response.json()) as AicfStatusPayload;
    return {
      payload,
      latencyMs: Math.max(0, Math.round(performance.now() - started)),
    };
  } finally {
    window.clearTimeout(timer);
  }
}

export function deriveAicfCounts(payload: AicfStatusPayload): AicfCounts {
  const counts = payload?.health?.counts ?? {};

  const users = asCount(counts.users);
  const projects = asCount(counts.projects);
  const providers = asCount(counts.providers);
  const nodes = asCount(counts.nodes);
  const jobs = asCount(counts.jobs);
  const contractJobs = asCount(counts.contractJobs);
  const agentTasks = asCount(counts.agentTasks);
  const usage = asCount(counts.usage);
  const settlements = asCount(counts.settlements);

  return {
    users,
    projects,
    providers,
    nodes,
    jobs,
    contractJobs,
    agentTasks,
    usage,
    settlements,
    queueUnits: jobs + contractJobs + agentTasks,
  };
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

export function formatUtc(isoTimestamp: string | undefined): string {
  if (!isoTimestamp) return 'Unavailable';
  const ts = Date.parse(isoTimestamp);
  if (!Number.isFinite(ts)) return 'Unavailable';
  return new Date(ts).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
}
