'use strict';
/**
 * BLOCK-REWARD SHARE LEASES.
 *
 * Sells a share of the TREASURY'S OWN 25% of each block over a block window,
 * priced against the NonKYC rate with a discount. It does NOT sell miners'
 * rewards, and it does NOT sell against the pool ledger — the pool has a
 * historical over-credit that cannot self-cure at a 0% fee, and selling
 * forward claims against a ledger in that state would be indefensible.
 * The treasury's share is a stream this operator actually controls and that
 * anyone can verify on-chain.
 *
 * DISABLED BY DEFAULT. A paid share of future block rewards is MORE
 * securities-flavoured than a spot sale, not less. That is an operator
 * decision, so the default is off and turning it on is deliberate.
 *
 * TWO INVARIANTS THIS FILE EXISTS TO HOLD:
 *
 *  1. OVERSUBSCRIPTION IS IMPOSSIBLE. Every lease overlapping a window is
 *     summed and checked against a ceiling INSIDE the same transaction that
 *     writes the new lease, so two concurrent buyers cannot both "fit".
 *     Selling 150% of a stream is fraud, not a bug to fix later.
 *
 *  2. PAY-AS-MINED, NEVER PROMISED. A lease accrues from blocks actually
 *     found in its window. A slow window pays less; it does not create a
 *     liability. The quote is therefore an ESTIMATE and says so in every
 *     response — quoting an expected yield as if it were owed is exactly how
 *     this product would become a debt we cannot honour.
 */

const crypto = require('node:crypto');
const { ProductError, ProductUnavailable } = require('./errors');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function nanmToAnm(n) {
  const v = BigInt(n);
  return `${v / 1000000000n}.${(v % 1000000000n).toString().padStart(9, '0')}`;
}

function createLeaseProduct({ cfg, node, gatewayStore, anmPrice, now = Date.now }) {
  const MAX_BPS = Number(cfg.leaseMaxSoldPct) * 100;      // percent -> bps
  const PER_BLOCK_NANM = BigInt(Math.round(Number(cfg.leaseTreasuryAnmPerBlock) * 1e9));

  async function head() {
    const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    if (!h || !Number.isInteger(h.height)) throw new ProductUnavailable('chain_head_unknown', 'chain.getHead returned no height');
    return h.height;
  }

  return {
    id: 'mining_lease',
    title: 'Block-reward share lease',
    description:
      `Lease a share of the Animica treasury's own 25% of every block for a window you choose (${cfg.leaseMinBlocks}..${cfg.leaseMaxBlocks} blocks), paid in ANM to an address you nominate. Priced against the live NonKYC rate with a ${cfg.leaseDiscountPercent}% discount. ` +
      'THIS IS PAY-AS-MINED: your lease accrues from blocks actually found inside its window, so a slow window pays less. The ANM figure in your quote is an ESTIMATE from a configured average block reward, not an amount owed to you, and nothing here is a promise of yield. ' +
      `Total leased share across all overlapping windows is hard-capped at ${cfg.leaseMaxSoldPct}% of the treasury share and is checked inside the same database transaction that records the sale, so overselling the stream is not possible. This sells the TREASURY's share only — never miners' rewards.`,
    path: '/x402/mining/lease',
    routes: [{ method: 'POST', path: '/x402/mining/lease' }],
    priceUsd: cfg.leasePriceUsd,
    enabled: cfg.leaseEnabled,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 8192,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          blocks: { type: 'integer', required: true, description: `window length in blocks, ${cfg.leaseMinBlocks}..${cfg.leaseMaxBlocks}` },
          payout_address: { type: 'string', required: true, description: 'anim1… bech32m address that accrued ANM is paid to' },
        },
      },
      output: {
        type: 'json',
        description: 'lease_id, share_bps, window {start_height, end_height, blocks}, estimated_anm, rate, discount_percent, capacity {sold_bps, ceiling_bps}, and the pay-as-mined disclosure',
      },
    },

    async availability() {
      if (!cfg.leaseEnabled) return { available: false, reason: 'lease_disabled' };
      const q = anmPrice.get();
      if (!q.ok) return { available: false, reason: q.reason, detail: q.detail };
      try {
        await head();
      } catch (e) {
        return { available: false, reason: 'node_unreachable', detail: e.message };
      }
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const blocks = b.blocks;
      if (!Number.isInteger(blocks)) throw bad('blocks must be an integer', 'invalid_request');
      if (blocks < Number(cfg.leaseMinBlocks) || blocks > Number(cfg.leaseMaxBlocks)) {
        throw bad(
          `blocks must be between ${cfg.leaseMinBlocks} and ${cfg.leaseMaxBlocks}`,
          'invalid_window',
          { caps: { min_blocks: Number(cfg.leaseMinBlocks), max_blocks: Number(cfg.leaseMaxBlocks) } }
        );
      }
      const addr = b.payout_address;
      if (typeof addr !== 'string' || !/^anim1[0-9a-z]{20,}$/.test(addr.trim())) {
        throw bad('payout_address must be a bech32m anim1… address', 'invalid_address');
      }
      return { blocks, payoutAddress: addr.trim() };
    },

    async preSettle(ctx) {
      const q = anmPrice.get();
      if (!q.ok) throw new ProductUnavailable(q.reason, q.detail);
      const h = await head();

      // Size the lease from what the buyer is paying, discounted.
      const quote = anmPrice.usdToNanm(this.priceUsd, { discountPercent: Number(cfg.leaseDiscountPercent) });
      if (!quote.ok) throw new ProductUnavailable(quote.reason, quote.detail);

      const { blocks } = ctx.params;
      const windowNanm = PER_BLOCK_NANM * BigInt(blocks);
      if (windowNanm <= 0n) {
        throw new ProductUnavailable('lease_misconfigured', 'the configured treasury reward per block is zero');
      }
      // share_bps = wanted / window * 10000, rounded UP so the buyer is never
      // short-changed by integer truncation.
      let shareBps = Number((quote.nanm * 10000n + windowNanm - 1n) / windowNanm);
      if (shareBps < 1) shareBps = 1;
      if (shareBps > MAX_BPS) {
        throw new ProductError(
          `a ${blocks}-block window is too short to deliver ${quote.anm_display} ANM within the ${cfg.leaseMaxSoldPct}% ceiling — choose a longer window`,
          {
            body: {
              error: 'window_too_short',
              detail: `required share ${shareBps} bps exceeds the ${MAX_BPS} bps ceiling`,
              suggestion: { min_blocks_for_this_price: Math.ceil(Number(quote.nanm * 10000n / (PER_BLOCK_NANM * BigInt(MAX_BPS)))) },
            },
          }
        );
      }

      const startHeight = h + 1;
      const endHeight = h + blocks;
      // Advisory pre-check. The BINDING check happens inside the write
      // transaction below; this one exists so an oversubscribed window is
      // refused BEFORE we take the money rather than after.
      const sold = gatewayStore.overlappingLeaseBps({ startHeight, endHeight });
      if (sold + shareBps > MAX_BPS) {
        throw new ProductError(
          'that window is already fully leased',
          {
            body: {
              error: 'oversubscribed',
              detail: `${sold} of ${MAX_BPS} bps are already sold across leases overlapping heights ${startHeight}-${endHeight}`,
              available_bps: Math.max(0, MAX_BPS - sold),
            },
          }
        );
      }

      return { head: h, startHeight, endHeight, shareBps, quote, windowNanm: windowNanm.toString() };
    },

    async handler(ctx) {
      const { blocks, payoutAddress } = ctx.params;
      const { startHeight, endHeight, shareBps, quote } = ctx.pinned;
      const leaseId = crypto.randomUUID();

      // THE BINDING CHECK. Sum-and-insert in ONE transaction: two buyers
      // racing for the last capacity cannot both succeed.
      const sale = gatewayStore.sellLeaseIfRoom({
        maxBps: MAX_BPS,
        lease: {
          leaseId,
          buyerAddress: payoutAddress,
          shareBps,
          startHeight,
          endHeight,
          paidUsd: this.priceUsd,
          quotedNanm: quote.nanm.toString(),
          rateUsdAnm: String(quote.usd_per_anm),
        },
      });
      if (!sale.ok) {
        // Money has already settled at this point, so this must be a loud,
        // retryable failure that lands in the incident path rather than a
        // silent nothing.
        const err = new Error(
          `lease could not be recorded: ${sale.reason} (${sale.availableBps} bps available). Payment settled — this is an incident, not a silent drop.`
        );
        err.retryable = false;
        throw err;
      }

      const estimated = (BigInt(ctx.pinned.windowNanm) * BigInt(shareBps)) / 10000n;

      return {
        status: 200,
        bodyObj: {
          product: 'mining_lease',
          lease_id: leaseId,
          payout_address: payoutAddress,
          share_bps: shareBps,
          share_percent: shareBps / 100,
          share_of: 'the treasury\'s own 25% of each block — NOT miners\' rewards',
          window: { start_height: startHeight, end_height: endHeight, blocks },
          pricing: {
            paid_usd: this.priceUsd,
            discount_percent: Number(cfg.leaseDiscountPercent),
            rate_usd_per_anm: String(quote.usd_per_anm),
            rate_source: quote.source,
            rate_side: 'bid',
            value_after_discount_usd: quote.usd_after_discount,
            target_anm: quote.anm_display,
          },
          estimate: {
            estimated_anm: nanmToAnm(estimated),
            estimated_nanm: estimated.toString(),
            assumed_treasury_anm_per_block: Number(cfg.leaseTreasuryAnmPerBlock),
            basis: 'a configured average treasury reward per block multiplied by the window length',
          },
          capacity: {
            sold_bps_after: sale.soldBpsAfter,
            ceiling_bps: MAX_BPS,
            ceiling_percent: Number(cfg.leaseMaxSoldPct),
            note: 'checked inside the same transaction that recorded this lease, so the stream cannot be oversold',
          },
          disclosure: {
            pay_as_mined:
              'This lease accrues ONLY from blocks actually found between start_height and end_height. If the network produces fewer blocks than assumed, you receive less. estimated_anm is an ESTIMATE, not an amount owed.',
            not_a_security_opinion:
              'No yield is promised and no return is guaranteed. This is a purchase of a share of a variable output stream over a fixed window.',
            settlement:
              'Accrual is recorded against this lease and paid to payout_address by the operator; this gateway holds no treasury key.',
          },
        },
      };
    },
  };
}

module.exports = { createLeaseProduct };
