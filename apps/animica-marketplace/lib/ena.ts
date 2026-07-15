import { config } from './config';

// Bridge to the ENA training coordinator: forward a consented preference (DPO triple) so the free
// model improves from real animica.dev usage. Fire-and-forget — feedback must never block the UI,
// and a coordinator outage must never fail the request.

export async function forwardPreferenceToEna(triple: {
  prompt: string;
  chosen: string;
  rejected: string;
  source?: string;
}): Promise<{ ok: boolean; total?: number; error?: string }> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const headers: Record<string, string> = { 'content-type': 'application/json' };
    if (config.enaApiToken) headers['authorization'] = 'Bearer ' + config.enaApiToken;
    const res = await fetch(`${config.enaCoordUrl}/feedback`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        prompt: triple.prompt.slice(0, 8000),
        chosen: triple.chosen.slice(0, 8000),
        rejected: triple.rejected.slice(0, 8000),
        source: triple.source || 'animica.dev',
        contributor: 'animica.dev',
      }),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!res.ok) return { ok: false, error: `ena ${res.status}` };
    const d = await res.json().catch(() => ({}));
    return { ok: true, total: d.total_feedback };
  } catch (e: any) {
    return { ok: false, error: e?.message || 'ena unreachable' };
  }
}
