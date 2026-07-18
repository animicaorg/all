import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { checkEntitlement } from '@/lib/entitlement';
import { generateImage } from '@/lib/media';
import { moderateMediaPrompt } from '@/lib/mediaModeration';
import { debitUsage, LedgerError } from '@/lib/ledger';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/mkt/v1/media/[slug]/generate { prompt, width?, height?, seed? }
// Generate media from a MEDIA listing. Access-gated + metered (USAGE) with the 80/20 split.
// Miners serve it; the marketplace credits its own ledger. Fail-closed — no placeholder images.
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'use');
    const body = await req.json();
    const prompt = String(body.prompt ?? '').trim();
    if (!prompt) throw new ApiError(400, 'bad_request', 'prompt required');
    const verdict = moderateMediaPrompt(prompt, { kind: 'image' });
    if (!verdict.allowed) throw new ApiError(422, verdict.code!, verdict.message!);

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      include: { prices: { where: { active: true } } },
    });
    if (!listing || listing.status !== 'PUBLISHED') throw new ApiError(404, 'not_found', 'listing not found');
    if (listing.type !== 'MEDIA') throw new ApiError(400, 'wrong_endpoint', 'not a media listing');
    if ((listing.mediaKind ?? 'image') !== 'image') {
      throw new ApiError(501, 'not_supported', `media kind '${listing.mediaKind}' not yet served (image is live)`);
    }

    const ent = await checkEntitlement(ctx.accountId, listing.id);
    if (!ent.entitled) throw new ApiError(402, 'not_entitled', 'purchase access first');

    const usagePrice = listing.prices.find((p) => p.model === 'USAGE');
    const metered = ent.priceModel === 'USAGE' && usagePrice && usagePrice.perCallNanm > 0n;
    const cost = metered ? usagePrice!.perCallNanm : 0n;

    // Generate (fail-closed).
    const result = await generateImage({
      prompt,
      tier: body.tier,
      width: Math.min(Number(body.width ?? 512), 1024),
      height: Math.min(Number(body.height ?? 512), 1024),
      steps: body.steps ? Number(body.steps) : undefined,
      seed: body.seed != null ? Number(body.seed) : undefined,
      negativePrompt: body.negative_prompt,
    });

    // Record usage + meter.
    const usageEvent = await prisma.usageEvent.create({
      data: {
        listingId: listing.id, accountId: ctx.accountId, kind: 'image',
        costNanm: cost, receiptId: result.sha3 || null,
      },
    });
    if (metered && cost > 0n) {
      try {
        await debitUsage(ctx.accountId, listing.ownerId, cost, listing.id, usageEvent.id);
      } catch (e) {
        if (e instanceof LedgerError) throw new ApiError(402, 'insufficient_funds', 'top up to generate');
        throw e;
      }
    }
    await prisma.listing.update({ where: { id: listing.id }, data: { usageCount: { increment: 1 } } }).catch(() => {});

    return ok({
      image: { b64_json: result.b64, mime: 'image/png' },
      model: result.model,
      receipt: result.sha3,
      dimensions: { width: result.width, height: result.height },
      served_by: result.device,
      latency_ms: result.latencyMs,
      usage: { cost_nanm: cost.toString() },
    });
  } catch (e) {
    if (e instanceof Error && /media worker|unavailable|ECONNREFUSED/i.test(e.message)) {
      return err(new ApiError(503, 'no_media_capacity', 'no media worker available right now — try again shortly'));
    }
    return err(e);
  }
}
