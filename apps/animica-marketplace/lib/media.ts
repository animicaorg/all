import { config } from './config';

// Client for the Animica media worker service (a miner serving generative media).
// Fail-closed: returns real image bytes/metadata or throws — never a placeholder.

export interface ImageResult {
  b64: string;       // base64 PNG
  model: string;
  sha3: string;      // content hash (Proof-of-Inference receipt anchor)
  width: number;
  height: number;
  latencyMs: number;
  device: string;
}

export async function generateImage(opts: {
  prompt: string;
  tier?: string;
  width?: number;
  height?: number;
  steps?: number;
  seed?: number;
  negativePrompt?: string;
  timeoutMs?: number;
}): Promise<ImageResult> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 180_000);
  try {
    const res = await fetch(`${config.mediaWorkerUrl}/v1/images/generations`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        prompt: opts.prompt,
        tier: opts.tier,
        width: opts.width ?? 512,
        height: opts.height ?? 512,
        steps: opts.steps,
        seed: opts.seed,
        negative_prompt: opts.negativePrompt,
      }),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`media worker ${res.status}: ${body.slice(0, 200)}`);
    }
    const data = await res.json();
    const b64 = data?.data?.[0]?.b64_json;
    if (!b64) throw new Error('media worker returned no image');
    const a = data.animica ?? {};
    return {
      b64,
      model: data.model,
      sha3: a.sha3 ?? '',
      width: a.width ?? opts.width ?? 512,
      height: a.height ?? opts.height ?? 512,
      latencyMs: a.latency_ms ?? 0,
      device: a.device ?? 'cpu',
    };
  } finally {
    clearTimeout(t);
  }
}

export async function mediaWorkerHealth(): Promise<{ ok: boolean; backend: string; capabilities: string[] }> {
  try {
    const res = await fetch(`${config.mediaWorkerUrl}/health`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return { ok: false, backend: `http ${res.status}`, capabilities: [] };
    const d = await res.json();
    return { ok: !!d.ok, backend: d.backend ?? '', capabilities: d.capabilities ?? [] };
  } catch (e: any) {
    return { ok: false, backend: e.message, capabilities: [] };
  }
}
