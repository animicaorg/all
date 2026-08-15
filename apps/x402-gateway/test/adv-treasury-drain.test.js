'use strict';
/**
 * ADVERSARIAL REVIEW — forced-sip drain and the slippage bound.
 *
 * FINDING D1 — the daily swap budget is a read-then-act check with a long
 * window and no lock. attemptSip() reads `sipSpendSince()` near the top and
 * only inserts its own row (`tstore.begin({kind:'sip'})`) after the approve
 * transaction has been signed, broadcast AND confirmed — up to
 * X402_RECEIPT_TIMEOUT_MS later. Any second caller that starts inside that
 * window sees a stale total and gets its own full allocation. In-process the
 * treasury's own `run()` mutex hides this, but the shipped CLI
 * (`animica-x402 treasury sip --confirm`) is a SECOND PROCESS on the same DB
 * with no mutex and no lock, and `force` deliberately bypasses the cooldown
 * that would otherwise catch it. Two callers => 2x the configured daily cap.
 *
 * FINDING D2 — the slippage bound is anchored to the same pool it protects.
 * `amountOutMinimum = QuoterV2(quote) * (1 - 1%)`, and the quote is a read of
 * the pool's current price. If the quote is wrong (manipulated pool, thin fee
 * tier, stale block), the bound is wrong by the same factor and the module
 * accepts the fill as a success. The only ABSOLUTE floor is the `uneconomic`
 * ratio, which at typical Base fees permits converting a $5 sip into ~$0.003
 * of ETH. docs/x402.md's "worst credible daily loss to slippage/MEV is cents"
 * is therefore an assumption about pool depth, not a bound the code enforces;
 * the enforced bound is the daily budget ($10/day).
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const { createTreasury } = require('../src/treasury/treasury');
const { createTreasuryStore } = require('../src/treasury/store');
const { createMetrics } = require('../src/metrics');
const { treasuryScene, decodeSipCalldata, decodeEip1559, C } = require('./treasury-helpers');
const { quietLogger } = require('./evm-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));

test('D1: a second caller (the CLI) doubles the daily swap budget through the read-then-act window', async () => {
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(30),
    env: { X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '5.00' }, // one $5 sip per day, by policy
  });

  // The CLI process: same key, same DB file, its own store handle, no lock.
  const cliTreasury = createTreasury({
    cfg: s.cfg,
    rpc: s.rpc,
    tstore: createTreasuryStore(s.store.db, { now: s.clock.now }),
    signer: s.signer,
    metrics: createMetrics(),
    logger: quietLogger,
    now: s.clock.now,
    sleep: s.clock.sleep,
  });

  // The operator runs `animica-x402 treasury sip --confirm` while the service
  // is already mid-sip (its approve is in flight). Neither sees the other's
  // intent, because the intent row is not written until the approve confirms.
  let fired = false;
  const inner = s.rpc.call.bind(s.rpc);
  s.rpc.call = async (method, params = []) => {
    const out = await inner(method, params);
    if (!fired && method === 'eth_estimateGas'
        && String(params[0].data).startsWith(uniswap.SELECTORS.approve)) {
      fired = true;
      const cli = await cliTreasury.sipNow();
      assert.equal(cli.action, 'sipped', 'the CLI saw a clean budget and sipped');
    }
    return out;
  };

  const service = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(service.action, 'sipped', 'and so did the service');

  const spent = s.tstore.sipSpendSince(0);
  assert.equal(spent, USDC(10), 'two $5 sips in one day against a $5/day budget');
  assert.ok(spent > s.cfg.treasury.dailySwapBudgetAtomic,
    `daily budget ${s.cfg.treasury.dailySwapBudgetAtomic} exceeded by ${spent - s.cfg.treasury.dailySwapBudgetAtomic} atomic units`);

  // Both rows are confirmed: this is not a race that fails safe, it is money
  // converted twice over the policy the operator wrote down.
  const rows = s.tstore.list({ kind: 'sip', limit: 10 });
  assert.equal(rows.length, 2);
  for (const r of rows) assert.equal(r.status, 'confirmed');
});

test('D1b: the same window exists for the cooldown — force skips it, and the row is written last', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  // A sip that has already run today: the cooldown should forbid another.
  const first = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(first.action, 'sipped');
  s.rpc.setEth(s.facilitator, 1n); // gas spent again, back under the floor
  const held = await s.treasury.attemptSip({ trigger: 'settlement' });
  assert.equal(held.reason, 'cooldown', 'the automatic path is correctly held');

  // The CLI is not.
  const forced = await s.treasury.sipNow();
  assert.equal(forced.action, 'sipped', 'force bypasses the cooldown by design — combined with D1 it also bypasses the budget accounting window');
  assert.equal(s.tstore.sipSpendSince(0), USDC(10));
});

test('D2: a wrong quote moves the slippage bound with it — a 90% haircut is accepted as a success', async () => {
  // The quoter reports 10% of the fair rate (a manipulated/thin pool, or a
  // stale price). amountOutMinimum is derived from that number alone.
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(30),
    chain: { quoteInflateBps: -9000 }, // quote = 10% of fair
  });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);

  // Model the matching bad fill: the swap returns exactly amountOutMinimum
  // (the worst fill the module will accept) instead of the fair amount.
  let minOut = null;
  const inner = s.rpc.call.bind(s.rpc);
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const tx = decodeEip1559(params[0]);
      if (evm.addressEquals(tx.to, C.swapRouter02)) {
        minOut = decodeSipCalldata(tx.data).swap.amountOutMinimum;
      }
      return inner(method, params);
    }
    const r = await inner(method, params);
    if (method === 'eth_getTransactionReceipt' && r && minOut !== null) {
      for (const log of r.logs) {
        if (evm.addressEquals(log.address, C.weth9) && log.topics[0] === uniswap.TOPICS.withdrawal) {
          log.data = '0x' + minOut.toString(16).padStart(64, '0');
        }
      }
    }
    return r;
  };

  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'sipped', 'the module reports a clean, successful sip');

  const fair = s.rpc.quoteOut(USDC(5), 100); // what $5 is really worth
  assert.ok(r.eth_received_wei * 10n < fair,
    `accepted ${r.eth_received_wei} wei for $5 of revenue; fair value was ${fair} wei (>10x more)`);

  // Nothing in the module compares the fill to any independent price: the
  // only absolute floor is the uneconomic ratio.
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'src', 'treasury', 'treasury.js'), 'utf8');
  assert.ok(!/ethUsdPrice|priceFloor|minWeiPerUsdc|oracle/i.test(src),
    'treasury.js has no independent price reference to sanity-check a quote against');

  // And the enforced daily exposure is the swap budget, not "cents".
  assert.equal(s.cfg.treasury.dailySwapBudgetAtomic, USDC(10));
});
