'use strict';
/**
 * ANM/USD reference price, read from the NonKYC feed this box already
 * maintains (`anm-price.timer` -> /var/www/animica.org/anm-price.json).
 *
 * THE RULE, inherited from Animica Pay: a stale rate must REFUSE to quote,
 * never silently quote at yesterday's number. Money converted at a dead rate
 * is a real loss in one direction and a real overcharge in the other, and the
 * failure is invisible unless the code fails closed. `anm-price.timer` is a
 * systemd timer; if it stops, this returns {ok:false} and every ANM-priced
 * surface answers 503 rather than guessing.
 *
 * We quote against the BID (what the market will actually pay for ANM), not
 * `last` or `mid` — taking the conservative side means a buyer paying in ANM
 * is never short-changing us because of spread.
 */

const fs = require('node:fs');

const DEFAULT_PATH = '/var/www/animica.org/anm-price.json';

function createAnmPrice({
  path = DEFAULT_PATH,
  maxAgeSeconds = 900,
  now = Date.now,
  readFile = fs.readFileSync,
} = {}) {
  let cache = { at: 0, value: null };

  function readRaw() {
    const txt = readFile(path, 'utf8');
    const j = JSON.parse(txt);
    // `is_indicative` marks a feed that is a guess rather than a trade.
    // Quoting real money off an indicative print is exactly the silent-wrong
    // behaviour this module exists to prevent.
    if (j.is_indicative === true) {
      return { ok: false, reason: 'price_indicative', detail: 'the ANM feed is marked indicative, not a traded price' };
    }
    const bid = Number(j.bid);
    if (!Number.isFinite(bid) || bid <= 0) {
      return { ok: false, reason: 'price_unparseable', detail: `bid was ${JSON.stringify(j.bid)}` };
    }
    const ts = Number(j.ts);
    if (!Number.isFinite(ts) || ts <= 0) {
      return { ok: false, reason: 'price_no_timestamp', detail: 'feed carried no usable ts' };
    }
    const ageSec = Math.floor(now() / 1000) - ts;
    if (ageSec > maxAgeSeconds) {
      return {
        ok: false,
        reason: 'price_stale',
        detail: `ANM price feed is ${ageSec}s old (limit ${maxAgeSeconds}s); refusing to quote rather than use a dead rate. Check: systemctl status anm-price.timer`,
        age_seconds: ageSec,
      };
    }
    return {
      ok: true,
      usd_per_anm: bid,
      bid,
      ask: Number(j.ask) || null,
      mid: Number(j.mid) || null,
      source: j.source || 'nonkyc',
      market_url: j.market_url || null,
      symbol: j.symbol || 'ANM/USDT',
      observed_at: new Date(ts * 1000).toISOString(),
      age_seconds: ageSec,
      side: 'bid',
    };
  }

  return {
    /** Cached 30s — the feed itself only refreshes on a timer. */
    get() {
      if (now() - cache.at < 30000 && cache.value) return cache.value;
      let v;
      try {
        v = readRaw();
      } catch (e) {
        v = { ok: false, reason: 'price_unavailable', detail: `cannot read ${path}: ${e.message}` };
      }
      cache = { at: now(), value: v };
      return v;
    },

    /**
     * USD (decimal string) -> nANM (BigInt), with an optional discount in
     * percent. Returns {ok, nanm, ...quote} or {ok:false, reason}.
     *
     * Integer math on the way out: the USD figure is scaled to micro-dollars
     * and the division happens in BigInt, so no float ever touches the
     * amount a payer is asked for. (A float here is the 1e9 unit bug this
     * codebase has already paid for once.)
     */
    usdToNanm(usd, { discountPercent = 0 } = {}) {
      const q = this.get();
      if (!q.ok) return q;
      const micros = usdToMicros(usd);
      if (micros === null) return { ok: false, reason: 'bad_usd_amount', detail: String(usd) };
      const afterDiscount = (micros * BigInt(100 - Number(discountPercent))) / 100n;
      // Scale the rate to PICO-dollars per ANM (1e12), not micro-dollars.
      // ANM trades near 0.00007271 USD, which is only ~72.71 micro-dollars —
      // rounding that to an integer 73 is a 0.4% error in the amount every
      // payer is charged. At 1e12 the rate keeps six significant figures.
      const picosPerAnm = BigInt(Math.round(q.usd_per_anm * 1e12));
      if (picosPerAnm <= 0n) return { ok: false, reason: 'price_unparseable', detail: 'rate rounded to zero' };
      // nANM = microUSD * 1e15 / picoUSD-per-ANM
      //      = (microUSD/1e6) / (pico/1e12) * 1e9
      const nanm = (afterDiscount * 1000000000000000n) / picosPerAnm;
      if (nanm <= 0n) return { ok: false, reason: 'amount_rounds_to_zero', detail: `${usd} USD is below one nANM at the current rate` };
      return Object.assign({}, q, {
        ok: true,
        nanm,
        nanm_string: nanm.toString(),
        anm_display: nanmToAnm(nanm),
        usd_before_discount: usd,
        discount_percent: Number(discountPercent),
        usd_after_discount: microsToUsd(afterDiscount),
      });
    },
  };
}

/** "0.0073" -> 7300n micro-dollars. Refuses anything that is not decimal. */
function usdToMicros(usd) {
  const s = String(usd).trim();
  if (!/^\d+(\.\d+)?$/.test(s)) return null;
  const [w, f = ''] = s.split('.');
  const frac = (f + '000000').slice(0, 6);
  return BigInt(w) * 1000000n + BigInt(frac);
}

function microsToUsd(micros) {
  const v = BigInt(micros);
  return `${v / 1000000n}.${(v % 1000000n).toString().padStart(6, '0')}`;
}

/** nANM BigInt -> "1.234567890" ANM. 1 ANM = 1e9 nANM. */
function nanmToAnm(nanm) {
  const v = BigInt(nanm);
  return `${v / 1000000000n}.${(v % 1000000000n).toString().padStart(9, '0')}`;
}

module.exports = { createAnmPrice, usdToMicros, microsToUsd, nanmToAnm, DEFAULT_PATH };
