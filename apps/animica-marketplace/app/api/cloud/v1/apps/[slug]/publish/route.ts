import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { requireEntitlement } from '@/lib/cloud/entitlements';
import { flags } from '@/lib/cloud/config';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/apps/[slug]/publish — validate EVERYTHING, then make the listing live.
//
// A published app is a promise to strangers, so publication is gated on:
//   * the marketplace_publishing entitlement (Developer plan and up, or a Founding grant),
//   * at least one PUBLISHED function with a deployed version attached to the app,
//   * the app declaring every capability its functions can request (users authorize the app,
//     so an undeclared function capability would be an authorization the user never saw),
//   * pricing consistency (paid models carry a price; metered/free models never do).

export async function POST(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    if (!flags.marketplace) throw new ApiError(503, 'disabled', 'the marketplace is not enabled on this node');
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');

    const slug = decodeURIComponent(ctx.params.slug).trim().toLowerCase();
    const app = await prisma.cloudApp.findUnique({
      where: { slug },
      include: {
        functions: {
          select: { id: true, slug: true, status: true, currentVersion: true, capabilities: true, suspendedAt: true },
        },
      },
    });
    if (!app || app.ownerId !== auth.accountId) throw new ApiError(404, 'not_found', 'no such app');
    if (app.suspendedAt) throw new ApiError(403, 'suspended', app.suspendedReason || 'this app is suspended');
    if (app.status === 'ARCHIVED') throw new ApiError(409, 'archived', 'archived apps cannot be published');

    await requireEntitlement(auth.accountId, 'marketplace_publishing');

    const problems: string[] = [];
    if (!app.name.trim()) problems.push('the app needs a name');
    if (!app.tagline.trim()) problems.push('the app needs a tagline — it is the one line buyers see in the catalog');
    if (!app.description.trim()) problems.push('the app needs a description');

    const live = app.functions.filter((f) => f.status === 'PUBLISHED' && f.currentVersion > 0 && !f.suspendedAt);
    if (live.length === 0) {
      problems.push('attach at least one PUBLISHED function with a deployed version before publishing');
    }
    const undeclared = new Set<string>();
    for (const f of live) for (const c of f.capabilities) if (!app.capabilities.includes(c)) undeclared.add(c);
    if (undeclared.size > 0) {
      problems.push(
        `the app must declare every capability its functions use — missing: ${[...undeclared].join(', ')}. ` +
          'Users authorize the APP, so nothing a function can do may be hidden from that consent.',
      );
    }
    if ((app.pricingModel === 'ONE_TIME' || app.pricingModel === 'SUBSCRIPTION') && app.priceNanm <= 0n) {
      problems.push(`${app.pricingModel} pricing requires priceNanm > 0`);
    }
    if ((app.pricingModel === 'FREE' || app.pricingModel === 'PAY_PER_USE') && app.priceNanm !== 0n) {
      problems.push(`${app.pricingModel} apps must have priceNanm = 0`);
    }

    if (problems.length > 0) {
      const e = new ApiError(422, 'not_publishable', 'the app is not ready to publish');
      e.details = { problems };
      throw e;
    }

    const updated = await prisma.cloudApp.update({
      where: { id: app.id },
      data: { status: 'PUBLISHED', publishedAt: app.publishedAt ?? new Date() },
    });

    return ok({
      published: true,
      app: {
        slug: updated.slug,
        status: updated.status,
        visibility: updated.visibility,
        publishedAt: updated.publishedAt,
        liveFunctions: live.map((f) => f.slug),
      },
      url: `/cloud/apps/${updated.slug}`,
    });
  } catch (e) {
    return err(e);
  }
}
