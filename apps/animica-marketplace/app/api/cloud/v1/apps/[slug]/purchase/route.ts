import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, withIdempotency, ApiError } from '@/lib/api';
import { settleAppPurchase, SettlementError, LedgerError } from '@/lib/cloud/settle';
import { feeBpsForDeveloper } from '@/lib/cloud/entitlements';
import { activePolicy } from '@/lib/cloud/pricing';
import { flags } from '@/lib/cloud/config';
import { findPublishedApp } from '../../_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/apps/[slug]/purchase — buy (ONE_TIME), subscribe (SUBSCRIPTION), or
// install (FREE / PAY_PER_USE, a 0-ANM purchase row that records the install).
//
// Money moves through settleAppPurchase(): exactly-once, exact-sum, all inside one ledger
// transaction. The marketplace fee rate is snapshotted per purchase via feeBpsForDeveloper()
// — a Founding Developer's reduced rate applies automatically and history never rewrites (§88).

const SUBSCRIPTION_DAYS = Math.max(1, Number(process.env.CLOUD_APP_SUBSCRIPTION_DAYS ?? 30));

export async function POST(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    if (!flags.marketplace) throw new ApiError(503, 'disabled', 'the marketplace is not enabled on this node');
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key to purchase');
    requireScope(auth, 'buy');

    const slug = decodeURIComponent(ctx.params.slug).trim().toLowerCase();
    const body = await req.json().catch(() => ({}));

    // `await` (not a bare return) so a handler throw is caught by THIS try and mapped by err().
    return await withIdempotency(req, auth, { slug, ...((body ?? {}) as object) }, async () => {
      const app = await findPublishedApp(slug);
      if (!app) throw new ApiError(404, 'not_found', 'no such app');

      const kind = app.pricingModel === 'SUBSCRIPTION' ? 'subscription' : 'one_time';
      const amountNanm = app.pricingModel === 'ONE_TIME' || app.pricingModel === 'SUBSCRIPTION' ? app.priceNanm : 0n;
      const expiresAt =
        kind === 'subscription' ? new Date(Date.now() + SUBSCRIPTION_DAYS * 24 * 3600 * 1000) : null;

      const policy = await activePolicy();
      const feeBps = await feeBpsForDeveloper(app.ownerId, policy.platformFeeBps);

      let result;
      try {
        result = await settleAppPurchase({
          appId: app.id,
          buyerAccountId: auth.accountId,
          developerAccountId: app.ownerId,
          amountNanm,
          feeBps,
          kind,
          expiresAt,
        });
      } catch (e) {
        if (e instanceof SettlementError && e.code === 'SELF_PURCHASE') {
          throw new ApiError(400, 'self_purchase', 'you are the developer of this app — it is already yours');
        }
        if (e instanceof LedgerError && e.code === 'INSUFFICIENT_FUNDS') {
          throw new ApiError(
            402,
            'insufficient_funds',
            `this ${kind === 'subscription' ? 'subscription period' : 'purchase'} costs ${amountNanm} nANM — deposit ANM and retry`,
          );
        }
        throw e;
      }

      const p = result.purchase;
      return {
        status: result.alreadyOwned ? 200 : 201,
        data: {
          purchased: !result.alreadyOwned,
          alreadyOwned: result.alreadyOwned,
          purchase: {
            id: p.id,
            app: app.slug,
            kind: p.kind,
            amountNanm: p.amountNanm,
            platformFeeNanm: p.platformFeeNanm,
            developerNanm: p.developerNanm,
            feeBps: p.feeBps,
            status: p.status,
            expiresAt: p.expiresAt,
            createdAt: p.createdAt,
          },
          pricingModel: app.pricingModel,
          nextStep:
            app.capabilities.length > 0
              ? `authorize the app's capabilities with POST /api/cloud/v1/apps/${app.slug}/authorize`
              : 'the app is ready to use',
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
