import { NextRequest } from 'next/server';
import { createHash } from 'node:crypto';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { forwardPreferenceToEna } from '@/lib/ena';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/feedback
// Capture user/agent feedback from animica.dev and (with consent) feed it into ENA self-improvement.
//  - kind "preference": { prompt, chosen, rejected } -> stored + forwarded to the ENA DPO corpus.
//  - kind "thumbs_up"/"thumbs_down": a single-response rating (stored; used for signal/filtering).
// Public (no key needed — the free chat calls it); attaches the account when authenticated. Raw
// prompt/response text is only persisted + forwarded when consent === true (default false).
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req).catch(() => null);
    const body = await req.json().catch(() => ({}));
    const kind = String(body.kind || '').trim();
    const allowed = ['thumbs_up', 'thumbs_down', 'regenerate', 'preference'];
    if (!allowed.includes(kind)) throw new ApiError(400, 'bad_kind', `kind must be one of ${allowed.join(', ')}`);

    const consent = body.consent === true;
    const prompt = typeof body.prompt === 'string' ? body.prompt : '';
    const chosen = typeof body.chosen === 'string' ? body.chosen : '';
    const rejected = typeof body.rejected === 'string' ? body.rejected : '';
    const promptHash = prompt ? createHash('sha256').update(prompt).digest('hex') : null;

    let listingId: string | null = null;
    if (body.listingSlug) {
      const l = await prisma.listing.findUnique({ where: { slug: String(body.listingSlug) }, select: { id: true } });
      listingId = l?.id ?? null;
    }

    // Only retain raw text when the user consented (privacy-first). Otherwise keep the hash + kind.
    const payload: any = { surface: body.surface || 'animica.dev', model: body.model || null };
    if (consent && kind === 'preference') { payload.prompt = prompt.slice(0, 8000); payload.chosen = chosen.slice(0, 8000); payload.rejected = rejected.slice(0, 8000); }

    const fb = await prisma.feedback.create({
      data: {
        accountId: ctx?.accountId ?? null,
        listingId,
        surface: String(body.surface || 'animica.dev'),
        kind,
        model: body.model ? String(body.model) : null,
        promptHash,
        payloadJson: JSON.stringify(payload),
        consent,
      },
      select: { id: true },
    });

    // Forward a consented DPO preference to the ENA training coordinator (fire-and-forget).
    let training: any = { forwarded: false };
    if (kind === 'preference' && consent && prompt && chosen && rejected && chosen !== rejected) {
      const r = await forwardPreferenceToEna({ prompt, chosen, rejected, source: 'animica.dev' });
      training = { forwarded: r.ok, ...(r.total ? { corpusTotal: r.total } : {}), ...(r.error ? { note: r.error } : {}) };
      if (r.ok) await prisma.feedback.update({ where: { id: fb.id }, data: { exported: true } }).catch(() => {});
    }

    return ok({ id: fb.id, kind, consent, training }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
