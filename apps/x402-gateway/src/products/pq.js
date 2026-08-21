'use strict';
/**
 * POST-QUANTUM SIGNATURE VERIFICATION.
 *
 * Nobody else in the x402 ecosystem sells this, because almost nobody else
 * runs a chain whose signatures are post-quantum. Animica's transaction
 * signing is ML-DSA-65 (and friends), and the verifier is already on this
 * box - so an agent can hand us a message, a public key and a signature and
 * get an authoritative yes/no without shipping a PQ library itself.
 *
 * HOW IT RUNS. The verifier is Python (animica.tx.crypto.verify), so each
 * call spawns the repo venv interpreter with a FIXED script and passes the
 * request as JSON ON STDIN - never as argv, and never through a shell. The
 * inputs are attacker-controlled by definition, so:
 *   - sizes are capped BEFORE the process is spawned;
 *   - the child gets a hard timeout and is killed on expiry;
 *   - concurrency is bounded, because "spawn a process per request" is a
 *     denial-of-service primitive if left unbounded;
 *   - the child's stdout is parsed as JSON and nothing else is trusted.
 *
 * A FAILED VERIFICATION IS A SUCCESSFUL CALL. "This signature is invalid" is
 * the answer the caller paid for, delivered as ok:false - not an error. Only
 * an inability to CHECK is a failure.
 */

const { spawn } = require('node:child_process');
const { ProductError, ProductUnavailable } = require('./errors');

const HEX = /^(0x)?[0-9a-fA-F]*$/;

// Fixed child program. Reads one JSON object on stdin, writes one on stdout.
const CHILD = [
  'import sys, json, binascii',
  'try:',
  '    from animica.tx.crypto import verify',
  'except Exception as e:',
  '    print(json.dumps({"error": "verifier_unavailable", "detail": str(e)})); sys.exit(0)',
  'try:',
  '    req = json.load(sys.stdin)',
  '    h = lambda s: binascii.unhexlify(s[2:] if s[:2] in ("0x","0X") else s)',
  '    r = verify(',
  '        alg_id=int(req["alg_id"]),',
  '        msg=h(req["message"]),',
  '        signature=h(req["signature"]),',
  '        pubkey=h(req["public_key"]),',
  '        sign_hash=h(req.get("sign_hash") or ""),',
  '    )',
  '    print(json.dumps({',
  '        "ok": bool(getattr(r, "ok", False)),',
  '        "reason": getattr(r, "reason", None),',
  '        "scheme_id": getattr(r, "scheme_id", None),',
  '        "pub_fingerprint": getattr(r, "pub_fingerprint", None),',
  '        "sign_hash": getattr(r, "sign_hash", None),',
  '    }))',
  'except Exception as e:',
  '    print(json.dumps({"error": "verify_failed", "detail": str(e)[:300]}))',
].join('\n');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function hexLen(s) {
  const t = String(s).replace(/^0x/i, '');
  return Math.floor(t.length / 2);
}

function createPqVerifyProduct({ cfg, now = Date.now, spawnImpl = spawn }) {
  // Bounded concurrency: spawning a process per request is a DoS primitive if
  // nothing caps it, and this endpoint is reachable by anyone who pays.
  let running = 0;

  function runChild(payload) {
    return new Promise((resolve, reject) => {
      let child;
      try {
        child = spawnImpl(cfg.pqPythonBin, ['-c', CHILD], {
          env: Object.assign({}, process.env, { PYTHONPATH: cfg.pqPythonPath }),
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (e) {
        return reject(new ProductUnavailable('pq_verifier_unavailable', `cannot start the verifier: ${e.message}`));
      }
      let out = '';
      let err = '';
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        reject(Object.assign(new Error(`verifier timed out after ${cfg.pqTimeoutMs}ms`), { retryable: false }));
      }, Number(cfg.pqTimeoutMs));

      child.stdout.on('data', (d) => {
        out += d;
        if (out.length > 65536) { try { child.kill('SIGKILL'); } catch { /* gone */ } }
      });
      child.stderr.on('data', (d) => { err += d.toString().slice(0, 2000); });
      child.on('error', (e) => {
        if (settled) return;
        settled = true; clearTimeout(timer);
        reject(new ProductUnavailable('pq_verifier_unavailable', e.message));
      });
      child.on('close', () => {
        if (settled) return;
        settled = true; clearTimeout(timer);
        let parsed = null;
        try { parsed = JSON.parse(out.trim().split('\n').pop()); } catch { /* handled below */ }
        if (!parsed) {
          return reject(Object.assign(
            new Error(`verifier produced no usable output${err ? ': ' + err.slice(0, 200) : ''}`),
            { retryable: true }
          ));
        }
        resolve(parsed);
      });
      child.stdin.end(JSON.stringify(payload));
    });
  }

  return {
    id: 'pq_verify',
    title: 'Post-quantum signature verification',
    description:
      'Verify a post-quantum signature (ML-DSA-65 / Dilithium3 / SPHINCS+ as enabled by chain policy) against a message and public key, using the same verifier the Animica chain itself uses to admit transactions. Useful when an agent must check a PQ-signed attestation without shipping a post-quantum library of its own. A signature that does not verify is a SUCCESSFUL call answering ok:false - you paid for the answer, and "invalid" is an answer. Only an inability to perform the check is treated as a failure.',
    path: '/x402/pq/verify',
    routes: [{ method: 'POST', path: '/x402/pq/verify' }],
    priceUsd: cfg.pqVerifyPriceUsd,
    enabled: cfg.pqEnabled,
    // Verify first, charge after: a caller must not pay when we could not run
    // the check at all.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 4 * 1024 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          alg_id: { type: 'integer', required: true, description: 'scheme id - 4099 (0x1003) is ML-DSA-65, the only scheme that can spend on Animica' },
          message: { type: 'string', required: true, description: 'hex-encoded message bytes' },
          signature: { type: 'string', required: true, description: 'hex-encoded signature' },
          public_key: { type: 'string', required: true, description: 'hex-encoded public key' },
          sign_hash: { type: 'string', required: false, description: 'hex domain/sign-hash bytes, when the scheme binds one' },
        },
      },
      output: {
        type: 'json',
        description: 'ok (the verdict), reason, scheme_id, scheme_name, pub_fingerprint, sizes, verified_at',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const algId = b.alg_id;
      if (!Number.isInteger(algId) || algId < 0 || algId > 0xffff) {
        throw bad('alg_id must be an integer scheme id (e.g. 4099 for ML-DSA-65)', 'invalid_request');
      }
      for (const f of ['message', 'signature', 'public_key']) {
        if (typeof b[f] !== 'string' || !b[f]) throw bad(`${f} is required and must be a hex string`, 'invalid_request');
        if (!HEX.test(b[f])) throw bad(`${f} must be hex`, 'invalid_hex', { field: f });
        if (String(b[f]).replace(/^0x/i, '').length % 2 !== 0) {
          throw bad(`${f} has an odd number of hex digits`, 'invalid_hex', { field: f });
        }
      }
      if (b.sign_hash !== undefined && b.sign_hash !== null && b.sign_hash !== '') {
        if (typeof b.sign_hash !== 'string' || !HEX.test(b.sign_hash)) throw bad('sign_hash must be hex', 'invalid_hex');
      }
      const msgBytes = hexLen(b.message);
      if (msgBytes > Number(cfg.pqMaxMessageBytes)) {
        throw bad(
          `message is ${msgBytes} bytes, over the ${cfg.pqMaxMessageBytes} cap - hash it and verify the digest instead`,
          'message_too_large'
        );
      }
      if (hexLen(b.signature) > 65536 || hexLen(b.public_key) > 65536) {
        throw bad('signature or public_key is larger than any supported scheme', 'invalid_request');
      }
      return {
        alg_id: algId,
        message: b.message,
        signature: b.signature,
        public_key: b.public_key,
        sign_hash: b.sign_hash || '',
        sizes: { message_bytes: msgBytes, signature_bytes: hexLen(b.signature), public_key_bytes: hexLen(b.public_key) },
      };
    },

    async handler(ctx) {
      if (running >= Number(cfg.pqMaxConcurrent)) {
        throw new ProductUnavailable(
          'pq_verifier_busy',
          `too many verifications in flight (limit ${cfg.pqMaxConcurrent}); retry shortly. Nothing was charged.`
        );
      }
      running++;
      let result;
      try {
        result = await runChild(ctx.params);
      } finally {
        running--;
      }
      if (result.error) {
        if (result.error === 'verifier_unavailable') {
          throw new ProductUnavailable('pq_verifier_unavailable', result.detail || 'the verifier could not be loaded');
        }
        // Malformed input discovered inside the verifier is the caller's
        // problem, and they must not be charged for it.
        throw bad(result.detail || 'verification could not be performed', 'verify_failed');
      }

      const SCHEME_NAMES = {
        1: 'dilithium3', 2: 'sphincs_shake_128s', 3: 'sphincs_shake_128f',
        4: 'sphincs_shake_256s', 4099: 'ml_dsa_65',
      };
      const schemeId = (result.scheme_id !== null && result.scheme_id !== undefined) ? result.scheme_id : ctx.params.alg_id;

      return {
        status: 200,
        bodyObj: {
          product: 'pq_verify',
          // THE VERDICT. false here means the signature did not verify - the
          // call succeeded and this is the answer.
          ok: Boolean(result.ok),
          reason: result.reason || null,
          scheme_id: schemeId,
          scheme_name: SCHEME_NAMES[schemeId] || null,
          pub_fingerprint: result.pub_fingerprint || null,
          sign_hash: result.sign_hash || null,
          sizes: ctx.params.sizes,
          verified_at: new Date(now()).toISOString(),
          verifier: 'animica.tx.crypto.verify - the same code path the Animica node uses to admit transactions',
          note:
            'ok:false is a completed verification with a negative result, not an error. Scheme 4099 (0x1003, ML-DSA-65) is the only scheme that can actually spend on Animica; other ids may verify cryptographically while being unspendable on-chain.',
        },
      };
    },
  };
}

module.exports = { createPqVerifyProduct, CHILD };
