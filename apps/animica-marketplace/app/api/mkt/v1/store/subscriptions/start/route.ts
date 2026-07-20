import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { config } from '@/lib/config';
import { settlePurchaseInTx, LedgerError } from '@/lib/ledger';
import { verifyWalletLogin } from '@/lib/wallet-verify';
import { validateChallengeV2, consumeChallengeV2 } from '@/lib/challenge';
import { licenseLeafHash } from '@/lib/license';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/subscriptions/start
// Start a CUSTODIAL subscription. HONESTY: the chain has no pull-payment/allowance primitive, so
// renewals debit the buyer's in-app MARKETPLACE LEDGER balance (withdrawable anytime, cancel
// anytime) — NEVER a non-custodial on-chain auto-pay. Each renewal happens ONLY because the buyer
// signed a StoreConsent up front; this route is what records that consent.
//
// Body: { slug, priceId?, address, challenge, signature, publicKey }
//   - `challenge` MUST be a v2 subscribe challenge:
//       animica-login|v2|purpose=subscribe|aud=<host>|listing=<slug>|period=<days>|amount=<nanm>|<addr>|<ts>|<mac>
//     Fetch it from GET /auth/challenge?address=..&purpose=subscribe&listing=<slug>&period=<days>&amount=<nanm>
//     so the economic terms the wallet SIGNS are exactly what the server enforces (a blind-signed
//     subscribe challenge can never be repurposed to a different listing/period/amount). The
//     signature is ml_dsa_65-verified (lib/wallet-verify) and the challenge is burned single-use.
//
// Effects (one transaction): record StoreConsent(purpose=subscribe) → first-period debit via the
// ledger split (settlePurchaseInTx, STORE_FEE_BPS for APP/DIGITAL_GOOD else MKT_FEE_BPS, fork
// royalty as-is) → Purchase(SUBSCRIPTION, autoRenew=true, consentId set, expiresAt=now+periodDays)
// → SUBSCRIPTION License for the period. The armed renewal worker then renews it each period
// (it requires exactly this shape: autoRenew=true + consentId + a StoreConsent row).
//
// 402 insufficient_funds => the wallet prompts a top-up (deposit address → deposit-watcher credit).
// Idempotent: an already-ACTIVE unexpired subscription for this listing is returned unchanged.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');
    const body = await req.json();

    return await withIdempotency(req, ctx, body, async () => {
      const slug = String(body.slug ?? '');
      const address = String(body.address ?? '').trim().toLowerCase();
      const challenge = String(body.challenge ?? '');
      const signatureHex = String(body.signature ?? '');
      const publicKeyHex = String(body.publicKey ?? '');

      const listing = await prisma.listing.findUnique({
        where: { slug },
        include: { prices: true, forkOf: { select: { ownerId: true } } },
      });
      if (!listing || listing.status !== 'PUBLISHED') throw new ApiError(404, 'not_found', 'listing not found');
      if (listing.ownerId === ctx.accountId) throw new ApiError(400, 'self_purchase', 'cannot subscribe to your own listing');

      const price = listing.prices.find((p) => p.id === body.priceId && p.active && p.model === 'SUBSCRIPTION')
        ?? listing.prices.find((p) => p.active && p.model === 'SUBSCRIPTION');
      if (!price) throw new ApiError(400, 'no_subscription_price', 'no active subscription price');
      if (price.amountNanm <= 0n) throw new ApiError(400, 'bad_price', 'subscription price must be > 0');
      if (!Number.isInteger(price.periodDays) || price.periodDays <= 0) throw new ApiError(400, 'bad_period', 'invalid subscription period');

      // The acting account must be the wallet that signs the consent — the challenge binds to
      // `address`, and the split debits the buyer's balance, so the two must be the same identity.
      const buyer = await prisma.account.findUnique({
        where: { id: ctx.accountId },
        select: { id: true, address: true, balanceNanm: true },
      });
      if (!buyer) throw new ApiError(401, 'unauthorized', 'account not found');
      if (buyer.address.toLowerCase() !== address) {
        throw new ApiError(400, 'address_mismatch', 'signed address does not match the authenticated account');
      }

      // Idempotency (session + retry): an ACTIVE, unexpired subscription for this listing already
      // exists => return it instead of double-charging. (Re-enabling a cancelled-but-still-active
      // subscription is not handled here — subscribe again after it expires.)
      const now = new Date();
      const existing = await prisma.purchase.findFirst({
        where: {
          buyerId: buyer.id, listingId: listing.id, priceModel: 'SUBSCRIPTION', status: 'ACTIVE',
          OR: [{ expiresAt: null }, { expiresAt: { gt: now } }],
          renewals: { none: {} },
        },
        orderBy: { createdAt: 'desc' },
      });
      if (existing) {
        return { status: 200, data: { subscription: shapeSub(existing, listing, price), entitled: true, alreadyActive: true, custodial: true, disclosure: DISCLOSURE } };
      }

      // 1) Verify the wallet signature BEFORE burning, so a bad signature never spends the challenge.
      const sig = verifyWalletLogin({ address, message: challenge, signatureHex, publicKeyHex });
      if (!sig.ok) throw new ApiError(401, 'bad_signature', sig.reason ?? 'signature invalid');

      // 2) Validate the challenge (purpose/audience/timestamp/mac) WITHOUT burning, then check that
      //    the signed consent params bind to exactly this listing/period/amount.
      const v = validateChallengeV2(challenge, address, 'subscribe');
      if (!v.ok) throw new ApiError(400, 'bad_challenge', v.reason ?? 'challenge invalid or expired');
      const p = v.challenge.params;
      const listingOk = p.listing === listing.slug || p.listing === listing.id;
      if (!listingOk) throw new ApiError(400, 'consent_mismatch', 'consent does not bind this listing');
      if (p.period !== String(price.periodDays)) throw new ApiError(400, 'consent_mismatch', 'consent period does not match the price');
      if (p.amount !== price.amountNanm.toString()) throw new ApiError(400, 'consent_mismatch', 'consent amount does not match the price');

      // 3) Fee is fail-closed for store listings (mirrors the renewal worker + payment watcher):
      //    an APP/DIGITAL_GOOD sale must never settle while STORE_FEE_BPS is unconfigured.
      const feeBps = feeBpsFor(listing.type);
      if (!Number.isFinite(feeBps) || feeBps < 0 || feeBps > 10_000) {
        throw new ApiError(503, 'store_unconfigured', 'STORE_FEE_BPS is not configured');
      }

      // 4) Balance pre-check: return 402 for the common insufficient-funds case WITHOUT burning the
      //    challenge, so the wallet can top up and retry with the same (still-fresh) consent. The
      //    authoritative check is inside settlePurchaseInTx below (guards a concurrent debit race).
      if (buyer.balanceNanm < price.amountNanm) {
        throw new ApiError(402, 'insufficient_funds', 'top up your balance to subscribe');
      }

      // 5) Burn the challenge single-use (atomic replay defense) — only after every check passed.
      const c = await consumeChallengeV2(challenge, address, 'subscribe');
      if (!c.ok) throw new ApiError(400, 'bad_challenge', c.reason ?? 'challenge invalid or already used');

      const expiresAt = new Date(now.getTime() + price.periodDays * 86400_000);

      let result;
      try {
        result = await prisma.$transaction(async (tx) => {
          const consent = await tx.storeConsent.create({
            data: {
              accountId: buyer.id,
              purpose: 'subscribe',
              message: challenge, // exact signed string (audit)
              signatureHex,
              pubkeyHex: publicKeyHex,
            },
          });

          // First-period debit + 70/30 (or 80/20) split + optional fork royalty, atomically.
          await settlePurchaseInTx(tx, {
            buyerId: buyer.id,
            creatorId: listing.ownerId,
            amountNanm: price.amountNanm,
            listingId: listing.id,
            forkParentCreatorId: listing.forkOf?.ownerId,
            forkRoyaltyBps: listing.forkOfId ? listing.forkRoyaltyBps : undefined,
            feeBps,
          });

          const purchase = await tx.purchase.create({
            data: {
              buyerId: buyer.id,
              listingId: listing.id,
              priceId: price.id,
              priceModel: 'SUBSCRIPTION',
              amountNanm: price.amountNanm,
              status: 'ACTIVE',
              source: 'balance',
              expiresAt,
              autoRenew: true, // renewal worker requires this + consentId + a StoreConsent row
              consentId: consent.id,
            },
          });
          await tx.storeConsent.update({ where: { id: consent.id }, data: { purchaseId: purchase.id } });

          // SUBSCRIPTION license for the period (buildId null = covers all builds). leafHash needs
          // the row id, so create-then-set — same discipline as the watcher / renewal worker.
          const lic = await tx.license.create({
            data: {
              purchaseId: purchase.id,
              buyerAddress: buyer.address,
              listingId: listing.id,
              buildId: null,
              kind: 'SUBSCRIPTION',
              expiresAt,
              leafHash: '',
            },
          });
          const leafHash = licenseLeafHash({
            licenseId: lic.id,
            purchaseTxid: null, // custodial-balance purchase (no on-chain ANMSTORE1 tx)
            buyerAddress: buyer.address,
            listingId: listing.id,
            buildId: null,
            buildSha3: null,
            certSha256: null,
            kind: 'SUBSCRIPTION',
            issuedAt: lic.issuedAt,
            expiresAt,
          });
          const license = await tx.license.update({ where: { id: lic.id }, data: { leafHash } });
          await tx.listing.update({ where: { id: listing.id }, data: { usersCount: { increment: 1 } } });
          return { purchase, license, consent };
        });
      } catch (e) {
        if (e instanceof LedgerError && e.code === 'INSUFFICIENT_FUNDS') {
          throw new ApiError(402, 'insufficient_funds', 'top up your balance to subscribe');
        }
        throw e;
      }

      return {
        status: 201,
        data: {
          subscription: shapeSub(result.purchase, listing, price),
          license: { id: result.license.id, kind: result.license.kind, expiresAt: result.license.expiresAt, leafHash: result.license.leafHash },
          consentId: result.consent.id,
          entitled: true,
          custodial: true,
          disclosure: DISCLOSURE,
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}

const DISCLOSURE =
  'This subscription is CUSTODIAL: renewals debit your in-app marketplace balance (not an on-chain ' +
  'auto-pay). Your balance is withdrawable anytime and each renewal happens only under your signed ' +
  'consent. Cancel anytime — access continues until the paid period ends.';

// APP / DIGITAL_GOOD sell at STORE_FEE_BPS (70/30); everything else at MKT_FEE_BPS. Must match the
// renewal worker's feeBpsFor so a subscription's first period and its renewals split identically.
function feeBpsFor(listingType: string): number {
  if (listingType === 'APP' || listingType === 'DIGITAL_GOOD') return config.storeFeeBps;
  return config.feeBps;
}

function shapeSub(purchase: any, listing: any, price: any) {
  return jsonSafe({
    id: purchase.id,
    listingId: listing.id,
    listing: { slug: listing.slug, name: listing.name, type: listing.type },
    priceId: price.id,
    priceModel: purchase.priceModel,
    amountNanm: purchase.amountNanm,
    periodDays: price.periodDays,
    status: purchase.status,
    autoRenew: purchase.autoRenew,
    source: purchase.source,
    expiresAt: purchase.expiresAt,
    graceUntil: purchase.graceUntil ?? null,
    consentId: purchase.consentId ?? null,
    createdAt: purchase.createdAt,
  });
}
