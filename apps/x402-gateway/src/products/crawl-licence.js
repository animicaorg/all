'use strict';
/**
 * PAID CRAWL — post-quantum crawl licences (ML-DSA-65).
 *
 * WHY A SIGNATURE BELONGS HERE AT ALL. The pass token is an HMAC: it proves
 * something to US and to nobody else. That is fine for spending a budget and
 * useless for the thing an AI company actually needs, which is evidence it can
 * show to a THIRD PARTY that the pages it trained on were licensed. A crawl
 * licence is that evidence: a statement of what was bought, from whom, at what
 * price, over what window, signed with the same ML-DSA-65 (FIPS 204, scheme id
 * 4099) the Animica chain admits transactions with — and verifiable by anyone
 * holding the public key, with no account here and no trust in us.
 *
 * WHY POST-QUANTUM SPECIFICALLY, AND WHY IT IS NOT A GIMMICK. A training-data
 * provenance claim is not a session token; it is an assertion someone may need
 * to stand behind for a decade or more, in a dispute that starts long after
 * the crawl. That is precisely the lifetime over which a classical signature
 * stops being evidence — anything signed with ECDSA today is retrospectively
 * forgeable by whoever gets a cryptographically-relevant quantum computer
 * first, and a receipt that can be forged later cannot prove anything now.
 * ML-DSA-65 is the one property this receipt genuinely needs that an HMAC or
 * an ECDSA signature cannot provide.
 *
 * THE LICENCE ATTESTS TO CONSUMPTION, NOT TO INTENT. It is issued from stored
 * state — a real pass, its real spend counter, the site's real published terms
 * — so it says what happened rather than what somebody asked us to write down.
 * The terms digest is included so the site cannot quietly restate its price
 * afterwards and make an honest crawler look like a thief.
 *
 * THE KEY. Generated once and persisted 0600, because keygen_sig() in this
 * build takes no seed and a per-boot key would make every licence issued
 * before the last restart unverifiable. The public half is served free at
 * /x402/crawl/pubkey; the secret half never leaves the child process.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const { ProductError, ProductUnavailable } = require('./errors');

/** Domain separator. Distinct from every other Animica signing context, so a
 *  licence can never be replayed as a transaction or an execute receipt. */
const SIGN_DOMAIN = 'animica-paid-crawl';
const CHAIN_ID = 1;
const ALG = 'ml_dsa_65';

/** Deterministic JSON: the signature covers these exact bytes, so key order
 *  must not depend on how the object happened to be built. */
function canonicalJson(obj) {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return `[${obj.map(canonicalJson).join(',')}]`;
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(',')}}`;
}

const KEYGEN_CHILD = [
  'import sys, json',
  'try:',
  '    from pq.py.keygen import keygen_sig',
  'except Exception as e:',
  '    print(json.dumps({"error":"pq_unavailable","detail":str(e)[:300]})); sys.exit(0)',
  'try:',
  '    kp = keygen_sig("ml_dsa_65")',
  '    print(json.dumps({"public_key": kp.public_key.hex(), "secret_key": kp.secret_key.hex()}))',
  'except Exception as e:',
  '    print(json.dumps({"error":"keygen_failed","detail":str(e)[:300]}))',
].join('\n');

const SIGN_CHILD = [
  'import sys, json, binascii',
  'try:',
  '    from pq.py.sign import sign_detached',
  'except Exception as e:',
  '    print(json.dumps({"error":"pq_unavailable","detail":str(e)[:300]})); sys.exit(0)',
  'try:',
  '    req = json.load(sys.stdin)',
  '    sk = binascii.unhexlify(req["secret_key"])',
  '    msg = req["message"].encode("utf-8")',
  '    s = sign_detached(msg, "ml_dsa_65", sk, domain=req["domain"], chain_id=int(req["chain_id"]))',
  '    print(json.dumps({"alg_id": s.alg_id, "alg": s.alg_name, "domain": s.domain,',
  '                      "prehash": s.prehash, "signature": s.sig.hex()}))',
  'except Exception as e:',
  '    print(json.dumps({"error":"sign_failed","detail":str(e)[:300]}))',
].join('\n');

// Rebuilds the Signature envelope from its parts and checks it. verify_detached
// RAISES on a domain/alg mismatch rather than returning False, so both are
// caught and reported as a negative result — "this does not verify" is an
// answer, not a crash.
const VERIFY_CHILD = [
  'import sys, json, binascii',
  'try:',
  '    from pq.py.sign import verify_detached, Signature',
  'except Exception as e:',
  '    print(json.dumps({"error":"pq_unavailable","detail":str(e)[:300]})); sys.exit(0)',
  'try:',
  '    req = json.load(sys.stdin)',
  '    sig = Signature(alg_id=int(req["alg_id"]), alg_name=req["alg"], domain=req["domain"],',
  '                    prehash=req["prehash"], sig=binascii.unhexlify(req["signature"]))',
  '    pk = binascii.unhexlify(req["public_key"])',
  '    msg = req["message"].encode("utf-8")',
  '    try:',
  '        ok = bool(verify_detached(msg, sig, pk, domain=req["domain"], chain_id=int(req["chain_id"])))',
  '        reason = None if ok else "invalid_signature"',
  '    except Exception as ve:',
  '        ok, reason = False, str(ve)[:200]',
  '    print(json.dumps({"ok": ok, "reason": reason}))',
  'except Exception as e:',
  '    print(json.dumps({"error":"verify_failed","detail":str(e)[:300]}))',
].join('\n');

function createCrawlLicence({ cfg, gatewayStore, spawnImpl = spawn, now = Date.now, logger = null }) {
  const keyPath = cfg.crawlLicenceKeyPath || path.join(__dirname, '..', '..', 'state', 'crawl-licence-key.json');
  const maxConcurrent = Number(cfg.crawlLicenceConcurrency || 2);
  let inflight = 0;
  let cachedKey = null;

  /** Run one bounded, non-shell child with JSON on stdin. */
  function runChild(script, payload, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
      if (inflight >= maxConcurrent) {
        reject(new ProductUnavailable('signer_busy', 'the post-quantum signer is at its concurrency limit; retry shortly'));
        return;
      }
      inflight += 1;
      let child;
      try {
        child = spawnImpl(cfg.pqPythonBin, ['-c', script], {
          env: { ...process.env, PYTHONPATH: cfg.pqPythonPath, PYTHONDONTWRITEBYTECODE: '1' },
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (e) {
        inflight -= 1;
        reject(new ProductUnavailable('signer_unavailable', e.message));
        return;
      }
      let out = '';
      let err = '';
      const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch (_e) { /* already gone */ } }, timeoutMs);
      child.stdout.on('data', (d) => { out += d; if (out.length > 512 * 1024) { try { child.kill('SIGKILL'); } catch (_e) { /* gone */ } } });
      child.stderr.on('data', (d) => { err += String(d).slice(0, 2000); });
      child.on('error', (e) => { clearTimeout(timer); inflight -= 1; reject(new ProductUnavailable('signer_unavailable', e.message)); });
      child.on('close', () => {
        clearTimeout(timer);
        inflight -= 1;
        let parsed;
        try { parsed = JSON.parse(out.trim()); } catch (_e) {
          reject(new ProductUnavailable('signer_unavailable', `unparseable signer output${err ? `: ${err.slice(0, 200)}` : ''}`));
          return;
        }
        if (parsed && parsed.error) { reject(new ProductUnavailable(parsed.error, parsed.detail || '')); return; }
        resolve(parsed);
      });
      try { child.stdin.end(JSON.stringify(payload)); } catch (_e) { /* close handler reports */ }
    });
  }

  /**
   * The signing key, generated once and reused forever. A per-boot key would
   * silently invalidate every licence issued before the last restart, which is
   * the one thing a provenance receipt may never do.
   */
  async function ensureKey() {
    if (cachedKey) return cachedKey;
    try {
      const raw = JSON.parse(fs.readFileSync(keyPath, 'utf8'));
      if (raw && raw.public_key && raw.secret_key) {
        cachedKey = raw;
        return cachedKey;
      }
    } catch (_e) { /* no key yet — generate one below */ }

    const kp = await runChild(KEYGEN_CHILD, {}, 60000);
    if (!kp.public_key || !kp.secret_key) throw new ProductUnavailable('keygen_failed', 'signer returned no key');
    const rec = {
      alg: ALG,
      alg_id: 4099,
      public_key: kp.public_key,
      secret_key: kp.secret_key,
      created_at: Math.floor(now() / 1000),
    };
    fs.mkdirSync(path.dirname(keyPath), { recursive: true });
    // Written 0600 and never logged. The public half is served freely; the
    // secret half exists only here and inside the signing child.
    fs.writeFileSync(keyPath, JSON.stringify(rec, null, 2), { mode: 0o600 });
    try { fs.chmodSync(keyPath, 0o600); } catch (_e) { /* best effort on odd filesystems */ }
    cachedKey = rec;
    if (logger && logger.info) logger.info('paid-crawl: generated ML-DSA-65 licence key', { public_key_sha256: sha256(kp.public_key).slice(0, 16) });
    return cachedKey;
  }

  function sha256(s) { return crypto.createHash('sha256').update(String(s)).digest('hex'); }

  /** Sign a licence body. Returns the body, the signature and the public key. */
  async function sign(body) {
    const key = await ensureKey();
    const message = canonicalJson(body);
    const s = await runChild(SIGN_CHILD, {
      secret_key: key.secret_key, message, domain: SIGN_DOMAIN, chain_id: CHAIN_ID,
    });
    return {
      licence: body,
      signed_bytes: message,
      signature: {
        alg: s.alg || ALG,
        alg_id: s.alg_id || 4099,
        scheme: 'ML-DSA-65 (FIPS 204)',
        domain: s.domain || SIGN_DOMAIN,
        chain_id: CHAIN_ID,
        prehash: s.prehash || 'sha3-512',
        signature: s.signature,
        public_key: key.public_key,
      },
      verify: {
        free_endpoint: 'POST https://animica.dev/x402/crawl/licence/verify',
        public_key_endpoint: 'GET https://animica.dev/x402/crawl/pubkey',
        offline: 'pip install animica; pq.py.sign.verify_detached(signed_bytes.encode(), Signature(...), bytes.fromhex(public_key), domain="animica-paid-crawl", chain_id=1)',
      },
    };
  }

  async function verify({ signedBytes, signature, publicKey, algId, alg, domain, prehash }) {
    const r = await runChild(VERIFY_CHILD, {
      message: String(signedBytes),
      signature: String(signature),
      public_key: String(publicKey),
      alg_id: Number(algId || 4099),
      alg: String(alg || ALG),
      domain: String(domain || SIGN_DOMAIN),
      prehash: String(prehash || 'sha3-512'),
      chain_id: CHAIN_ID,
    });
    return { ok: !!r.ok, reason: r.reason || null };
  }

  /**
   * Build the licence body for a pass from STORED state. Nothing here is
   * caller-supplied except which pass to describe — a receipt that repeated
   * back whatever the buyer claimed would attest to nothing.
   */
  function licenceBodyFor({ pass, site }) {
    const termsDigest = sha256(canonicalJson({
      domain: site.domain,
      price_usd_per_page: site.price_usd,
      free_per_day: Number(site.free_per_day),
      unknown_policy: site.unknown_policy,
      operator_share_bps: Number(site.operator_share_bps),
    }));
    return {
      statement: 'The bearer purchased crawl access to this domain under the terms recorded below. This licence attests to a settled payment and to pages actually consumed under it.',
      type: 'animica.paid-crawl.licence/v1',
      issuer: 'https://animica.dev/x402/crawl',
      domain: pass.domain,
      pass_id: pass.pass_id,
      pages_licensed: Number(pass.requests_total),
      pages_consumed: Number(pass.requests_used),
      price_usd_per_page: String(pass.price_usd),
      paid_usd: String(pass.paid_usd),
      payer: pass.payer || null,
      payment_fingerprint: pass.payment_fingerprint || null,
      window_start: new Date(Number(pass.issued_at) * 1000).toISOString(),
      window_end: new Date(Number(pass.expires_at) * 1000).toISOString(),
      site_terms_digest_sha256: termsDigest,
      site_owner_verified: Number(site.verified) === 1,
      issued_at: new Date(now()).toISOString(),
    };
  }

  // -------------------------------------------------------------- free routes
  function licenceRoute() {
    return {
      method: 'POST',
      path: '/x402/crawl/licence',
      title: 'Paid Crawl — claim a post-quantum crawl licence (free)',
      description:
        'FREE. Exchange a crawl pass for a post-quantum-signed licence: a portable, independently verifiable record of what you paid for, which domain, at what price and over what window. Signed with ML-DSA-65 (FIPS 204, scheme 4099) — the evidence stays valid after classical signatures stop being evidence.',
      bodyFields: {
        pass: { type: 'string', required: true, description: 'the anmcp_ crawl pass to turn into a licence; may be sent as an X-Crawl-Pass header instead' },
      },
      match(p) { return p === '/x402/crawl/licence' ? {} : null; },
      async handler(ctx) {
        const b = ctx.json || {};
        const token = b.pass || (ctx.headers && (ctx.headers['x-crawl-pass'] || ctx.headers['X-Crawl-Pass']));
        if (!token) return { status: 400, bodyObj: { error: 'missing_pass', detail: 'send {"pass":"anmcp_..."} — a licence describes a real settled pass' } };
        const { tokenHashOf } = require('./crawl-gate');
        const pass = gatewayStore.getCrawlPass(tokenHashOf(String(token)));
        if (!pass) return { status: 404, bodyObj: { error: 'unknown_pass', detail: 'no pass matches that token' } };
        const site = gatewayStore.getCrawlSite(pass.domain);
        if (!site) return { status: 409, bodyObj: { error: 'domain_deregistered', detail: 'the domain this pass covers no longer publishes terms' } };
        try {
          const out = await sign(licenceBodyFor({ pass, site }));
          return { status: 200, bodyObj: { product: 'paid_crawl_licence', cost: 'free', ...out } };
        } catch (e) {
          if (e instanceof ProductUnavailable) return { status: 503, bodyObj: { error: e.reason, detail: e.message } };
          return { status: 500, bodyObj: { error: 'licence_failed', detail: e.message } };
        }
      },
    };
  }

  function verifyRoute() {
    return {
      method: 'POST',
      path: '/x402/crawl/licence/verify',
      title: 'Paid Crawl — verify a crawl licence (free)',
      // A licence is ~11KB by construction (6.6KB signature hex + 3.9KB public
      // key hex). The parent pass product caps bodies at 4KB, which would
      // reject every genuine licence.
      maxBodyBytes: 64 * 1024,
      description:
        'FREE, and free on purpose: a receipt nobody can check for free is not evidence. Verify any Animica crawl licence — send signed_bytes, signature and public_key exactly as issued.',
      // `signature` and `public_key` are read from a nested `signature` object
      // if present, and from the top level otherwise — the licence is issued in
      // the nested shape, so that is what this documents.
      bodyFields: {
        signed_bytes: { type: 'string', required: true, description: 'the licence body exactly as issued, byte for byte' },
        signature: { type: 'object', required: false, description: '{ signature, public_key } as issued — ML-DSA-65 (FIPS 204, scheme 4099) hex. Send this OR the two flat fields below.' },
        public_key: { type: 'string', required: false, description: 'flat alternative to signature.public_key' },
      },
      match(p) { return p === '/x402/crawl/licence/verify' ? {} : null; },
      async handler(ctx) {
        const b = ctx.json || {};
        const sig = b.signature && typeof b.signature === 'object' ? b.signature : b;
        if (!b.signed_bytes || !sig.signature || !sig.public_key) {
          return { status: 400, bodyObj: { error: 'missing_fields', required: ['signed_bytes', 'signature.signature', 'signature.public_key'] } };
        }
        try {
          const r = await verify({
            signedBytes: b.signed_bytes,
            signature: sig.signature,
            publicKey: sig.public_key,
            algId: sig.alg_id,
            alg: sig.alg,
            domain: sig.domain,
            prehash: sig.prehash,
          });
          const key = await ensureKey().catch(() => null);
          let parsed = null;
          try { parsed = JSON.parse(String(b.signed_bytes)); } catch (_e) { parsed = null; }
          return {
            status: 200,
            bodyObj: {
              product: 'paid_crawl_licence_verify',
              cost: 'free',
              ok: r.ok,
              reason: r.reason,
              // Verifying the maths is only half of it: a valid signature by
              // SOMEBODY ELSE'S key proves nothing about this issuer.
              issued_by_this_gateway: !!(key && key.public_key === String(sig.public_key)),
              scheme: 'ML-DSA-65 (FIPS 204, scheme id 4099)',
              licence: parsed,
            },
          };
        } catch (e) {
          if (e instanceof ProductUnavailable) return { status: 503, bodyObj: { error: e.reason, detail: e.message } };
          return { status: 500, bodyObj: { error: 'verify_failed', detail: e.message } };
        }
      },
    };
  }

  function pubkeyRoute() {
    return {
      method: 'GET',
      path: '/x402/crawl/pubkey',
      title: 'Paid Crawl — ML-DSA-65 licence signing key (free)',
      description: 'FREE. The ML-DSA-65 public key every Animica crawl licence is signed with, so licences can be verified offline without calling us at all.',
      match(p) { return p === '/x402/crawl/pubkey' ? {} : null; },
      async handler() {
        try {
          const key = await ensureKey();
          return {
            status: 200,
            bodyObj: {
              product: 'paid_crawl_pubkey',
              scheme: 'ML-DSA-65 (FIPS 204)',
              alg: ALG,
              alg_id: 4099,
              sign_domain: SIGN_DOMAIN,
              chain_id: CHAIN_ID,
              prehash: 'sha3-512',
              public_key: key.public_key,
              public_key_sha256: sha256(key.public_key),
              created_at: new Date(Number(key.created_at) * 1000).toISOString(),
              note: 'the same post-quantum scheme the Animica L1 admits transactions with',
            },
          };
        } catch (e) {
          return { status: 503, bodyObj: { error: e.reason || 'signer_unavailable', detail: e.message } };
        }
      },
    };
  }

  return {
    sign, verify, ensureKey, licenceBodyFor, canonicalJson,
    freeRoutes: [licenceRoute(), verifyRoute(), pubkeyRoute()],
  };
}

module.exports = { createCrawlLicence, canonicalJson, SIGN_DOMAIN, CHAIN_ID, ALG };
