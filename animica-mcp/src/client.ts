/**
 * Client for the Animica OpenAI-compatible API (https://pool.animica.org/v1).
 *
 * Zero-config trial: if no ANIMICA_API_KEY is set, the first call auto-provisions
 * a FREE trial key from the gateway and caches it (~/.animica/mcp-key.json) — so
 * `npx animica-mcp` works instantly with no signup. When the trial endpoint isn't
 * available yet, calls fail with a clear "get a free key" message instead.
 */
import { homedir } from 'node:os';
import { join } from 'node:path';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

export const BASE_URL = (process.env.ANIMICA_BASE_URL || 'https://pool.animica.org/v1').replace(/\/$/, '');
export const POOL_URL = (process.env.ANIMICA_POOL_URL || 'https://pool.animica.org').replace(/\/$/, '');
export const STRATUM_URL = process.env.ANIMICA_STRATUM_URL || 'stratum+tcp://pool.animica.org:3333';
const TRIAL_URL = process.env.ANIMICA_TRIAL_URL || `${BASE_URL}/keys/trial`;
const TIMEOUT_MS = parseInt(process.env.ANIMICA_TIMEOUT_MS || '120000', 10);
const CACHE_FILE = join(homedir(), '.animica', 'mcp-key.json');

export class AnimicaError extends Error {
  constructor(message: string, readonly status?: number) { super(message); }
}

let memKey: string | null = process.env.ANIMICA_API_KEY || null;

function readCachedKey(): string | null {
  try { return JSON.parse(readFileSync(CACHE_FILE, 'utf8')).api_key || null; } catch { return null; }
}
function writeCachedKey(key: string) {
  try { mkdirSync(join(homedir(), '.animica'), { recursive: true }); writeFileSync(CACHE_FILE, JSON.stringify({ api_key: key, acquired_at: Date.now() }, null, 2)); } catch { /* best effort */ }
}

/** Resolve an API key: env → on-disk cache → auto free-trial. Memoized. */
async function resolveKey(): Promise<string> {
  if (memKey) return memKey;
  const cached = readCachedKey();
  if (cached) { memKey = cached; return cached; }
  if ((process.env.ANIMICA_AUTO_TRIAL ?? '1') !== '0') {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 30000);
      const resp = await fetch(TRIAL_URL, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ source: 'mcp' }), signal: ctrl.signal });
      clearTimeout(t);
      if (resp.ok) {
        const data = await resp.json().catch(() => ({}));
        const key = data.api_key || data.key;
        if (key) { memKey = key; writeCachedKey(key); console.error(`[animica-mcp] provisioned a free trial key (${data.credits_usd ? '$' + data.credits_usd + ' free credits' : 'free credits'}). Add ANIMICA_API_KEY or top up at ${POOL_URL} for more.`); return key; }
      }
    } catch { /* trial unavailable — fall through */ }
  }
  return '';
}

async function headers(key: string, extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const h: Record<string, string> = { 'content-type': 'application/json', ...extra };
  if (key) h['authorization'] = `Bearer ${key}`;
  return h;
}

async function req(path: string, init: RequestInit): Promise<any> {
  const key = await resolveKey();
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, { ...init, headers: await headers(key, init.headers as any), signal: ctrl.signal });
  } catch (e: any) {
    throw new AnimicaError(`Could not reach Animica at ${BASE_URL}${path}: ${e.message}`);
  } finally {
    clearTimeout(t);
  }
  const text = await resp.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-json */ }
  if (!resp.ok) {
    const msg = data?.error?.message || data?.message || text.slice(0, 300) || `HTTP ${resp.status}`;
    const hint = resp.status === 401 ? ` (no API key — get a FREE one in seconds at ${POOL_URL}, then set ANIMICA_API_KEY)`
      : resp.status === 402 ? ` (out of credits — top up at ${POOL_URL}/billing)`
      : resp.status === 404 ? ' (this endpoint is not yet available on your Animica gateway)'
      : resp.status === 429 ? ' (rate limited — slow down or upgrade your plan)' : '';
    throw new AnimicaError(`Animica API error ${resp.status}: ${msg}${hint}`, resp.status);
  }
  return data;
}

export interface ChatMessage { role: 'system' | 'user' | 'assistant'; content: string; }

export async function chat(opts: { messages: ChatMessage[]; model?: string; max_tokens?: number; temperature?: number; }): Promise<{ text: string; usage?: any; model: string }> {
  const model = opts.model || process.env.ANIMICA_MODEL || 'anm-fast-8b';
  const data = await req('/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ model, messages: opts.messages, max_tokens: opts.max_tokens ?? 1024, temperature: opts.temperature ?? 0.4, stream: false }),
  });
  return { text: data?.choices?.[0]?.message?.content ?? '', usage: data?.usage, model };
}

export async function embed(opts: { input: string | string[]; model?: string }): Promise<any> {
  return req('/embeddings', { method: 'POST', body: JSON.stringify({ model: opts.model || 'anm-embed', input: opts.input }) });
}

export async function listModels(): Promise<string[]> {
  const data = await req('/models', { method: 'GET' });
  return (data?.data || []).map((m: any) => m.id).filter(Boolean);
}

export async function gatewayPost(path: string, body: any): Promise<any> {
  return req(path, { method: 'POST', body: JSON.stringify(body) });
}
export async function gatewayGet(path: string): Promise<any> {
  return req(path, { method: 'GET' });
}

/** GET against the pool/mining API (provider side). Tries paths in order; no key needed. */
export async function poolGet(paths: string | string[]): Promise<any> {
  const list = Array.isArray(paths) ? paths : [paths];
  let lastErr: unknown = null;
  for (const p of list) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    try {
      const resp = await fetch(`${POOL_URL}${p}`, { headers: { accept: 'application/json' }, signal: ctrl.signal });
      const text = await resp.text();
      if (resp.ok) { try { return text ? JSON.parse(text) : {}; } catch { return { raw: text.slice(0, 2000) }; } }
      lastErr = new AnimicaError(`pool ${p} → HTTP ${resp.status}`, resp.status);
    } catch (e) { lastErr = e; } finally { clearTimeout(t); }
  }
  throw lastErr instanceof Error ? lastErr : new AnimicaError('pool API unavailable');
}
