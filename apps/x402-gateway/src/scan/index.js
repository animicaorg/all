'use strict';
/**
 * ANM 402 SCAN — a public directory of x402 services that settle in ANM,
 * plus the adoption bounty that pays operators to open their products to it.
 *
 * WHY BOTH LIVE IN ONE MODULE. They share the prober: a bounty is only
 * payable for a service that actually answers a real 402 advertising an
 * animica:* lane, which is exactly what a directory listing has to prove too.
 * One implementation, so a listing and a bounty can never disagree about
 * whether an endpoint is real.
 *
 * EVERY ROUTE HERE IS FREE. Charging to be listed, or to read the directory,
 * would defeat the point — the directory exists to make the ANM lane worth
 * adopting, and a toll booth on discovery is the opposite of that.
 *
 * ANTI-FARMING, because a bounty paid on a probe is trivially farmable
 * (anyone can serve a static 402 document):
 *   - the probe must reach a REAL 402 with an animica:* accepts entry whose
 *     payTo is a plausible address and is NOT ours;
 *   - one open claim per host, enforced by a UNIQUE INDEX in the database
 *     rather than a check-then-insert race;
 *   - the treasury must actually be able to cover the claim plus everything
 *     already reserved, or the programme stops accepting;
 *   - and NOTHING is ever paid automatically. This process holds no treasury
 *     key. A verified claim is reserved for a human to sign off.
 */

const crypto = require('node:crypto');
const { resolveSafely, parseTarget } = require('../products/web');

const ANIMICA_NET_RE = /^animica:/i;

function json(res, status, obj, headers) {
  const body = JSON.stringify(obj, null, 2);
  res.writeHead(status, Object.assign({ 'content-type': 'application/json' }, headers));
  res.end(body);
}

function hostOf(u) {
  try { return new URL(u).host.toLowerCase(); } catch { return null; }
}

function clientIp(req) {
  const xff = req.headers['x-forwarded-for'];
  if (typeof xff === 'string' && xff.trim()) return xff.split(',')[0].trim();
  return (req.socket && req.socket.remoteAddress) || 'unknown';
}

async function readJsonBody(req, maxBytes = 16384) {
  const chunks = [];
  let total = 0;
  for await (const c of req) {
    total += c.length;
    if (total > maxBytes) { const e = new Error('body too large'); e.status = 413; throw e; }
    chunks.push(c);
  }
  if (!total) return null;
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return null; }
}

function createScanService({ cfg, gatewayStore, node, fetchImpl = fetch, now = Date.now, logger = null }) {
  const log = logger || { info() {}, warn() {}, error() {} };

  /**
   * Probe a URL and decide whether it is a live ANM-settling x402 service.
   *
   * The bar is deliberately concrete: an HTTP 402 whose accepts array (v2) or
   * body (v1) carries an `animica:*` network. Anything less is "not an ANM
   * x402 service", however healthy the host may be.
   */
  async function probe(rawUrl) {
    let u;
    try {
      u = parseTarget(rawUrl);
    } catch (e) {
      return { ok: false, status: 'rejected', reason: 'invalid_url', detail: e.message };
    }
    // The URL is submitted by a stranger, so the same SSRF rules the fetch
    // product uses apply here: resolve it and refuse private space, or this
    // directory becomes an internal-network scanner for anyone who asks.
    try {
      await resolveSafely(u.hostname);
    } catch (e) {
      return { ok: false, status: 'rejected', reason: 'blocked_address', detail: e.message };
    }

    let res;
    let bodyText = '';
    try {
      res = await fetchImpl(u.toString(), {
        method: 'GET',
        redirect: 'follow',
        headers: {
          'user-agent': 'AnimicaX402Scan/1.0 (+https://animica.dev/x402/scan)',
          accept: 'application/json',
        },
        signal: AbortSignal.timeout(Number(cfg.scanProbeTimeoutMs)),
      });
      bodyText = await res.text();
    } catch (e) {
      return { ok: false, status: 'dead', reason: 'unreachable', detail: e.message };
    }

    if (res.status !== 402) {
      return {
        ok: false,
        status: 'dead',
        reason: 'no_402',
        detail: `expected HTTP 402 Payment Required, got ${res.status}. Point us at a PAID route, not a catalog or landing page.`,
      };
    }

    let body = null;
    try { body = JSON.parse(bodyText); } catch { /* header-only 402 is still possible */ }

    // Accepts can arrive in the body (v1/v2) or base64 in the header.
    let accepts = [];
    if (body && Array.isArray(body.accepts)) accepts = body.accepts;
    if (!accepts.length) {
      const hdr = res.headers.get('payment-required') || res.headers.get('www-authenticate');
      if (hdr) {
        try {
          const decoded = JSON.parse(Buffer.from(hdr, 'base64').toString('utf8'));
          if (Array.isArray(decoded.accepts)) accepts = decoded.accepts;
        } catch { /* not base64 json */ }
      }
    }
    if (!accepts.length) {
      return { ok: false, status: 'dead', reason: 'no_accepts', detail: 'the 402 carried no accepts array we could read' };
    }

    const anm = accepts.find((a) => a && typeof a.network === 'string' && ANIMICA_NET_RE.test(a.network));
    if (!anm) {
      return {
        ok: false,
        status: 'dead',
        reason: 'no_anm_lane',
        detail: `the 402 offers ${accepts.map((a) => a && a.network).filter(Boolean).join(', ') || 'nothing'} but no animica:* lane. Add an ANM accepts entry to be listed here.`,
        networks: accepts.map((a) => a && a.network).filter(Boolean),
      };
    }
    if (!anm.payTo || typeof anm.payTo !== 'string') {
      return { ok: false, status: 'dead', reason: 'no_pay_to', detail: 'the animica lane has no payTo address' };
    }

    return {
      ok: true,
      status: 'live',
      network: anm.network,
      payTo: anm.payTo,
      priceNanm: String(anm.maxAmountRequired || anm.amount || ''),
      asset: anm.asset || 'ANM',
      name: (body && (body.name || body.service_name)) || null,
      description: (body && body.description) || (anm.description || null),
      httpMethod: 'GET',
    };
  }

  /** Register (or re-probe) a service. FREE. */
  async function handleRegister(req, res) {
    const ip = clientIp(req);
    const sinceHour = Math.floor(now() / 1000) - 3600;
    const recent = gatewayStore.countScanSubmissionsSince(ip, sinceHour);
    if (recent >= Number(cfg.scanRegisterPerHour)) {
      return json(res, 429, {
        error: 'rate_limited',
        detail: `at most ${cfg.scanRegisterPerHour} registrations per hour per client`,
      });
    }
    const counts = gatewayStore.countScanServices();
    if (counts.total >= Number(cfg.scanMaxServices)) {
      return json(res, 503, { error: 'directory_full', detail: 'the directory has reached its configured capacity' });
    }

    let body;
    try {
      body = await readJsonBody(req);
    } catch (e) {
      return json(res, e.status || 400, { error: 'bad_body', detail: e.message });
    }
    if (!body || typeof body.url !== 'string' || !body.url.trim()) {
      return json(res, 400, {
        error: 'invalid_request',
        detail: 'send {"url": "https://…/your-paid-route"} — the PAID route that answers 402, not your catalog',
      });
    }

    const url = body.url.trim();
    const host = hostOf(url);
    if (!host) return json(res, 400, { error: 'invalid_url', detail: 'could not parse a host from that URL' });

    const result = await probe(url);
    const existing = gatewayStore.getScanServiceByUrl(url);
    const at = Math.floor(now() / 1000);

    if (existing) {
      gatewayStore.updateScanProbe({
        serviceId: existing.service_id,
        verified: result.ok,
        status: result.ok ? 'live' : result.status,
        lastProbeAt: at,
        lastOkAt: result.ok ? at : null,
        probeDetail: result.ok ? 'ok' : `${result.reason}: ${result.detail}`,
        failCount: result.ok ? 0 : Number(existing.fail_count || 0) + 1,
        priceNanm: result.priceNanm,
        payTo: result.payTo,
        network: result.network,
        asset: result.asset,
        name: result.name,
      });
      return json(res, 200, {
        action: 're-probed',
        service_id: existing.service_id,
        url,
        listed: result.ok,
        status: result.ok ? 'live' : result.status,
        detail: result.ok ? undefined : result.detail,
      });
    }

    // A URL we REFUSED to fetch at all (unparseable, or pointing into private
    // space) is not a service and must not occupy a row: storing it would let
    // anyone fill the directory with garbage that never becomes listable.
    // A reachable endpoint that merely lacks an ANM lane IS stored, because
    // that is a real service the operator can fix and re-probe.
    if (result.status === 'rejected') {
      return json(res, 400, {
        error: 'not_registrable',
        reason: result.reason,
        detail: result.detail,
      });
    }

    const serviceId = crypto.randomUUID();
    gatewayStore.putScanService({
      serviceId,
      url,
      host,
      name: (typeof body.name === 'string' ? body.name.slice(0, 120) : null) || result.name,
      description: (typeof body.description === 'string' ? body.description.slice(0, 500) : null) || result.description,
      provider: typeof body.provider === 'string' ? body.provider.slice(0, 120) : null,
      contact: typeof body.contact === 'string' ? body.contact.slice(0, 200) : null,
      httpMethod: 'GET',
      priceNanm: result.priceNanm,
      priceDisplay: result.priceNanm ? nanmDisplay(result.priceNanm) : null,
      payTo: result.payTo,
      network: result.network,
      asset: result.asset,
      category: typeof body.category === 'string' ? body.category.slice(0, 60) : null,
      verified: result.ok,
      status: result.ok ? 'live' : result.status,
      lastProbeAt: at,
      lastOkAt: result.ok ? at : null,
      probeDetail: result.ok ? 'ok' : `${result.reason}: ${result.detail}`,
      submittedBy: ip,
    });
    log.info('scan_registered', { host, listed: result.ok, reason: result.reason });

    return json(res, result.ok ? 201 : 202, {
      action: 'registered',
      service_id: serviceId,
      url,
      listed: result.ok,
      status: result.ok ? 'live' : result.status,
      detail: result.ok ? undefined : result.detail,
      next: result.ok
        ? 'Your service is listed. It is re-probed periodically; if it stops answering a 402 with an animica lane it is marked dead.'
        : 'Fix the issue above and POST the same URL again to re-probe. Nothing is listed until a probe succeeds.',
      bounty: cfg.bountyEnabled
        ? { claim: 'POST /x402/bounty/claim {"url":"…","payout_address":"anim1…"}', amount_usd: cfg.bountyAmountUsd }
        : undefined,
    });
  }

  function nanmDisplay(nanm) {
    try {
      const v = BigInt(nanm);
      return `${v / 1000000000n}.${(v % 1000000000n).toString().padStart(9, '0')} ANM`;
    } catch { return null; }
  }

  function publicService(row) {
    return {
      service_id: row.service_id,
      url: row.url,
      host: row.host,
      name: row.name,
      description: row.description,
      provider: row.provider,
      category: row.category,
      network: row.network,
      asset: row.asset,
      pay_to: row.pay_to,
      price_nanm: row.price_nanm,
      price_display: row.price_display,
      status: row.status,
      verified: Boolean(row.verified),
      last_probe_at: row.last_probe_at ? new Date(row.last_probe_at * 1000).toISOString() : null,
      last_ok_at: row.last_ok_at ? new Date(row.last_ok_at * 1000).toISOString() : null,
      probe_detail: row.probe_detail,
      fail_count: row.fail_count,
    };
  }

  function handleList(req, res, url) {
    const status = url.searchParams.get('status');
    const limit = Math.min(Number(url.searchParams.get('limit') || 100) || 100, 500);
    const offset = Math.max(Number(url.searchParams.get('offset') || 0) || 0, 0);
    const rows = gatewayStore.listScanServices({ status: status || null, limit, offset });
    const counts = gatewayStore.countScanServices();
    return json(res, 200, {
      directory: 'Animica 402 Scan',
      description: 'Public directory of x402 services that settle natively in ANM. Free to list, free to read. Every entry is PROBED — "live" means we fetched it and got a real 402 advertising an animica:* lane, not that someone told us so.',
      total: counts.total,
      live: counts.live,
      count: rows.length,
      services: rows.map(publicService),
      register: 'POST /x402/scan/register {"url":"https://…/your-paid-route"}',
      self: { gateway: 'https://animica.dev/x402', network: cfg.anmNetworkId },
    }, { 'cache-control': 'public, max-age=60' });
  }

  // -------------------------------------------------------------------------
  // Adoption bounty
  // -------------------------------------------------------------------------

  /** Treasury balance in nANM, or null when unreadable. */
  async function treasuryBalance() {
    try {
      const r = await node.call('state.getAddressBalance', { address: cfg.bountyTreasuryAddress }, { timeoutMs: 6000 });
      const raw = r && (r.confirmed_balance ?? r.balance ?? r.amount);
      if (raw === undefined || raw === null) return null;
      return typeof raw === 'string' && raw.startsWith('0x') ? BigInt(raw) : BigInt(String(raw));
    } catch {
      return null;
    }
  }

  /**
   * Can the treasury cover another claim? "Assuming the treasury can cover
   * it" is the whole gate: we stop ACCEPTING rather than promise what cannot
   * be paid, and everything already reserved counts against the balance.
   */
  async function budget(amountNanm) {
    const balance = await treasuryBalance();
    const reserved = gatewayStore.reservedBountyNanm();
    const reserve = BigInt(cfg.bountyTreasuryReserveAnm) * 1000000000n;
    if (balance === null) {
      return { ok: false, reason: 'treasury_unreadable', detail: 'could not read the treasury balance; refusing to promise a payout we cannot verify' };
    }
    const spendable = balance > reserve ? balance - reserve : 0n;
    const need = reserved.nanm + BigInt(amountNanm);
    if (need > spendable) {
      return {
        ok: false,
        reason: 'budget_exhausted',
        detail: 'the treasury cannot currently cover another bounty on top of what is already reserved',
        balance_nanm: balance.toString(),
        reserved_nanm: reserved.nanm.toString(),
        keep_reserve_nanm: reserve.toString(),
        spendable_nanm: spendable.toString(),
      };
    }
    return {
      ok: true,
      balance_nanm: balance.toString(),
      reserved_nanm: reserved.nanm.toString(),
      spendable_nanm: spendable.toString(),
      remaining_after_nanm: (spendable - need).toString(),
    };
  }

  async function handleBountyStatus(req, res, anmPrice) {
    const q = anmPrice.get();
    const amount = q.ok ? anmPrice.usdToNanm(cfg.bountyAmountUsd) : { ok: false };
    const b = amount.ok ? await budget(amount.nanm) : { ok: false, reason: 'price_unavailable' };
    const awarded = gatewayStore.countAwardedBounties();
    return json(res, 200, {
      programme: 'Animica ANM-x402 adoption bounty',
      enabled: Boolean(cfg.bountyEnabled),
      mode: cfg.bountyMode,
      what: 'Open one of your own products to ANM-native x402 payment (a paid route that answers 402 with an animica:* accepts entry), then claim.',
      amount_usd: cfg.bountyAmountUsd,
      amount_anm: amount.ok ? amount.anm_display : null,
      rate_usd_per_anm: q.ok ? String(q.usd_per_anm) : null,
      rate_source: q.ok ? q.source : null,
      claims_awarded: awarded,
      max_claims: Number(cfg.bountyMaxClaims),
      funding: b,
      how_to_claim: 'POST /x402/bounty/claim {"url":"https://…/your-paid-route","payout_address":"anim1…"}',
      payout_policy:
        'Claims are VERIFIED automatically and RESERVED. Payment itself is signed by a human operator — this service holds no treasury key, and paying automatically on a probe would be trivially farmable.',
      mode_note: cfg.bountyMode === 'open'
        ? 'OPEN: any verified claim is reserved while the budget lasts.'
        : 'CLOSED: claims are accepted and verified, but an operator must approve each one before it is reserved.',
    });
  }

  async function handleBountyClaim(req, res, anmPrice) {
    if (!cfg.bountyEnabled) {
      return json(res, 503, { error: 'bounty_disabled', detail: 'the adoption bounty is not currently running' });
    }
    let body;
    try {
      body = await readJsonBody(req);
    } catch (e) {
      return json(res, e.status || 400, { error: 'bad_body', detail: e.message });
    }
    if (!body || typeof body.url !== 'string' || typeof body.payout_address !== 'string') {
      return json(res, 400, {
        error: 'invalid_request',
        detail: 'send {"url":"https://…/your-paid-route","payout_address":"anim1…"}',
      });
    }
    const url = body.url.trim();
    const payout = body.payout_address.trim();
    const host = hostOf(url);
    if (!host) return json(res, 400, { error: 'invalid_url' });
    if (!/^anim1[0-9a-z]{20,}$/.test(payout)) {
      return json(res, 400, {
        error: 'invalid_payout_address',
        detail: 'payout_address must be a bech32m anim1… Animica address',
      });
    }
    if (Number(cfg.bountyMaxClaims) > 0 && gatewayStore.countAwardedBounties() >= Number(cfg.bountyMaxClaims)) {
      return json(res, 503, { error: 'bounty_closed', detail: 'the programme has reached its claim limit' });
    }
    if (cfg.bountyOnePerDomain) {
      const prior = gatewayStore.getBountyClaimByHost(host);
      if (prior) {
        return json(res, 409, {
          error: 'already_claimed',
          detail: `there is already a ${prior.status} claim for ${host}`,
          claim_id: prior.claim_id,
        });
      }
    }

    // Price it FIRST so a stale feed refuses before we probe anyone.
    const amount = anmPrice.usdToNanm(cfg.bountyAmountUsd);
    if (!amount.ok) {
      return json(res, 503, { error: amount.reason, detail: amount.detail || 'cannot price the bounty right now' });
    }
    const b = await budget(amount.nanm);
    if (!b.ok) {
      return json(res, 503, Object.assign({ error: b.reason, detail: b.detail }, b));
    }

    const result = await probe(url);
    if (!result.ok) {
      return json(res, 422, {
        error: 'not_eligible',
        reason: result.reason,
        detail: result.detail,
        requirement: 'the URL must answer HTTP 402 with an accepts entry whose network is animica:*',
      });
    }
    // Paying ourselves would be absurd; refuse a claim that points at our own
    // payTo address.
    if (String(result.payTo).toLowerCase() === String(cfg.anmPayTo).toLowerCase()) {
      return json(res, 422, {
        error: 'not_eligible',
        detail: 'that endpoint pays to this gateway\'s own address — the bounty is for opening YOUR product to ANM',
      });
    }

    const claimId = crypto.randomUUID();
    const status = cfg.bountyMode === 'open' ? 'verified' : 'pending';
    const stored = gatewayStore.putBountyClaim({
      claimId,
      url,
      host,
      payoutAddress: payout,
      amountUsd: cfg.bountyAmountUsd,
      amountNanm: amount.nanm.toString(),
      rateUsdAnm: String(amount.usd_per_anm),
      status,
    });
    if (!stored.ok) {
      return json(res, 409, { error: 'already_claimed', detail: `a claim for ${host} already exists` });
    }
    log.info('bounty_claim', { host, status, amount_nanm: amount.nanm.toString() });

    return json(res, 201, {
      claim_id: claimId,
      status,
      host,
      url,
      payout_address: payout,
      amount_usd: cfg.bountyAmountUsd,
      amount_anm: amount.anm_display,
      rate_usd_per_anm: String(amount.usd_per_anm),
      verified_lane: { network: result.network, pay_to: result.payTo, price_nanm: result.priceNanm },
      next:
        status === 'verified'
          ? 'Verified and reserved against the treasury. An operator signs the payout — this service holds no treasury key.'
          : 'Verified the endpoint, and recorded the claim. The programme is in CLOSED mode, so an operator must approve it before it is reserved.',
      check: `GET /x402/bounty/claim/${claimId}`,
    });
  }

  /** Re-probe the stalest listings. Cheap, bounded, and never throws. */
  async function recheck(limit = 5) {
    try {
      const cutoff = Math.floor(now() / 1000) - Math.floor(Number(cfg.scanRecheckIntervalMs) / 1000);
      const rows = gatewayStore.staleScanServices(cutoff, limit);
      for (const row of rows) {
        const r = await probe(row.url);
        const at = Math.floor(now() / 1000);
        gatewayStore.updateScanProbe({
          serviceId: row.service_id,
          verified: r.ok,
          status: r.ok ? 'live' : r.status,
          lastProbeAt: at,
          lastOkAt: r.ok ? at : null,
          probeDetail: r.ok ? 'ok' : `${r.reason}: ${r.detail}`,
          failCount: r.ok ? 0 : Number(row.fail_count || 0) + 1,
          priceNanm: r.priceNanm,
          payTo: r.payTo,
          network: r.network,
          asset: r.asset,
          name: r.name,
        });
      }
      return rows.length;
    } catch (e) {
      log.warn('scan_recheck_failed', { error: e.message });
      return 0;
    }
  }

  /**
   * Dispatch. Returns true when the request was handled here.
   * Every route is free; nothing in this module takes payment.
   */
  async function handle(req, res, url, path, anmPrice) {
    if (!cfg.scanEnabled && path.startsWith('/x402/scan')) return false;

    if (req.method === 'POST' && path === '/x402/scan/register') {
      await handleRegister(req, res);
      return true;
    }
    if (req.method === 'GET' && (path === '/x402/scan' || path === '/x402/scan/services')) {
      handleList(req, res, url);
      return true;
    }
    if (req.method === 'GET' && path.startsWith('/x402/scan/service/')) {
      const id = path.slice('/x402/scan/service/'.length);
      const row = gatewayStore.getScanService(id);
      if (!row) { json(res, 404, { error: 'not_found' }); return true; }
      json(res, 200, publicService(row));
      return true;
    }
    if (req.method === 'GET' && path === '/x402/bounty') {
      await handleBountyStatus(req, res, anmPrice);
      return true;
    }
    if (req.method === 'POST' && path === '/x402/bounty/claim') {
      await handleBountyClaim(req, res, anmPrice);
      return true;
    }
    if (req.method === 'GET' && path.startsWith('/x402/bounty/claim/')) {
      const id = path.slice('/x402/bounty/claim/'.length);
      const row = gatewayStore.getBountyClaim(id);
      if (!row) { json(res, 404, { error: 'not_found' }); return true; }
      json(res, 200, {
        claim_id: row.claim_id, status: row.status, host: row.host, url: row.url,
        payout_address: row.payout_address, amount_usd: row.amount_usd,
        amount_nanm: row.amount_nanm, rate_usd_per_anm: row.rate_usd_anm,
        reason: row.reason, payout_txid: row.payout_txid,
        created_at: new Date(Number(row.created_at) * 1000).toISOString(),
      });
      return true;
    }
    return false;
  }

  return { handle, probe, recheck, budget, treasuryBalance };
}

module.exports = { createScanService };
