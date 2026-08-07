import { NextRequest } from 'next/server';
import { ok, err, authenticate } from '@/lib/api';
import { trackBillingEvent } from '@/lib/plan';
import { isPlanKey } from '@/lib/planConfig';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Funnel analytics beacon (pricing views, checkout abandonment/errors). PII-light: only an
// allowlisted kind + optional plan key + the caller's accountId when signed in. Fire-and-
// forget semantics — this endpoint never fails the page.
const KINDS = new Set(['pricing_view', 'checkout_abandoned', 'checkout_error']);

export async function POST(req: NextRequest) {
  try {
    let body: any = {};
    try { body = await req.json(); } catch {}
    const kind = String(body?.kind || '');
    if (!KINDS.has(kind)) return ok({ ignored: true });
    const planKey = isPlanKey(String(body?.planKey || '')) ? String(body.planKey) : undefined;
    let accountId: string | undefined;
    try {
      const ctx = await authenticate(req);
      accountId = ctx?.accountId;
    } catch {
      // rate-limited key etc — beacon still counts anonymously
    }
    await trackBillingEvent(kind, { accountId, planKey });
    return ok({ ok: true });
  } catch (e) {
    return err(e);
  }
}
