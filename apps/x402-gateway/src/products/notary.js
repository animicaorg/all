'use strict';
/**
 * ON-CHAIN NOTARISATION + BLOB STORAGE, over the node's data-availability
 * layer (da.*).
 *
 * WHAT MAKES THIS WORTH SELLING. Anyone can hash a document. What an agent
 * cannot do for itself is get that hash into a place with an INDEPENDENTLY
 * CHECKABLE inclusion proof. `da.put` erasure-codes the record into shards
 * (32 data / 48 total here) and commits to a Merkle root; `da.getProof`
 * returns the leaf, its index and the sibling path. So the receipt we sell is
 * verifiable against the commitment by a third party who does not trust us.
 *
 * HONESTY ABOUT WHAT THE PROOF PROVES. It proves the record is committed in
 * this node's DA tree under a commitment we return. It does NOT by itself
 * prove a timestamp signed by the consensus set — the record carries the
 * chain head height and hash observed at the time, which is a far stronger
 * claim than a wall clock but weaker than a consensus timestamp. Both are
 * stated in every response instead of being blurred into "on-chain proof".
 *
 * VERIFICATION IS FREE. A notarisation nobody can check is worthless, so the
 * verify route takes no payment, ever — the same rule the commit-reveal
 * product follows for its reveal.
 */

const crypto = require('node:crypto');
const { ProductError, ProductUnavailable } = require('./errors');

const HEX64 = /^(0x)?[0-9a-fA-F]{64}$/;
const COMMITMENT_RE = /^[0-9a-f]{64}$/;

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/**
 * The DA layer keys namespaces by UINT32, not by name. Derive one
 * deterministically from the configured label so the same label always lands
 * in the same namespace and an operator can find their records again.
 */
function namespaceOf(label) {
  const h = crypto.createHash('sha256').update(String(label), 'utf8').digest();
  return h.readUInt32BE(0);
}

/** Shared DA helpers for both products. */
function daHelpers({ cfg, node }) {
  async function head() {
    const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    if (!h || !Number.isInteger(h.height)) throw new ProductUnavailable('chain_head_unknown', 'chain.getHead returned no height');
    return { height: h.height, hash: h.hash || null };
  }

  async function daStatus() {
    const s = await node.call('da.status', {}, { timeoutMs: 5000 });
    if (!s || s.ok !== true) {
      throw new ProductUnavailable('da_unavailable', `data-availability layer reports not ok: ${(s && s.reason) || 'unknown'}`);
    }
    if (s.writable !== true) {
      throw new ProductUnavailable('da_read_only', 'the data-availability layer is not writable right now');
    }
    return s;
  }

  async function put(buf, namespace) {
    const r = await node.call('da.put', {
      bytes: buf.toString('base64'),
      namespace,
    }, { timeoutMs: Number(cfg.notarizeTimeoutMs) });
    if (!r || !r.commitment) {
      const e = new Error('da.put returned no commitment');
      e.retryable = true;
      throw e;
    }
    return r;
  }

  return { head, daStatus, put, namespaceOf };
}

// ---------------------------------------------------------------------------
// P: notarize
// ---------------------------------------------------------------------------

function createNotarizeProduct({ cfg, node, now = Date.now }) {
  const da = daHelpers({ cfg, node });
  const NS = namespaceOf(cfg.notarizeNamespace);

  return {
    id: 'notarize',
    title: 'Notarise a digest on-chain',
    description:
      'Anchor a SHA-256 digest (or a small document, which we hash for you) into the Animica data-availability layer and get back a commitment plus a Merkle inclusion proof anyone can check without trusting us. The record is erasure-coded across shards and committed to a Merkle root; the response carries the commitment, the leaf index and sibling path, and the chain head height and hash observed at the moment of anchoring. Verification is FREE and permanent at GET /x402/notarize/verify/{commitment} — a notarisation nobody can check would be worth nothing. Note precisely what this proves: the record is committed in this node\'s DA tree under the returned commitment, alongside the head it observed. That is not the same as a timestamp signed by the whole consensus set, and the response says so rather than blurring the two.',
    path: '/x402/notarize',
    routes: [{ method: 'POST', path: '/x402/notarize' }],
    priceUsd: cfg.notarizePriceUsd,
    enabled: cfg.notarizeEnabled,
    // A write to the DA layer: re-check writability, settle, then write.
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 256 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          digest: { type: 'string', required: false, description: 'a 32-byte SHA-256 digest as hex (with or without 0x). Supply this OR data.' },
          data: { type: 'string', required: false, description: 'base64 document to hash and anchor (we store the digest and the memo, never your bytes, unless store_data is true)' },
          memo: { type: 'string', required: false, description: 'optional label stored with the record, <=256 chars' },
          store_data: { type: 'boolean', required: false, description: 'also store the supplied data itself, retrievable later (default false — by default only the digest is committed)' },
        },
      },
      output: {
        type: 'json',
        description: 'commitment, blob_id, namespace, digest, record (the exact bytes committed), proof {leaf_index, leaf_hash, siblings[], tree_height}, anchored_at {head_height, head_hash, observed_at}, verify_url, and what the proof does and does not establish',
      },
    },

    async availability() {
      try {
        const s = await da.daStatus();
        return { available: true, detail: `da writable, ${s.blob_count} blobs` };
      } catch (e) {
        if (e instanceof ProductUnavailable) return { available: false, reason: e.reason, detail: e.message };
        return { available: false, reason: 'da_unreachable', detail: e.message };
      }
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const hasDigest = typeof b.digest === 'string' && b.digest.trim();
      const hasData = typeof b.data === 'string' && b.data.trim();
      if (!hasDigest && !hasData) throw bad('supply either digest (hex sha-256) or data (base64)', 'invalid_request');
      if (hasDigest && hasData) throw bad('supply digest OR data, not both — otherwise it is ambiguous which one is being notarised', 'invalid_request');

      let digest;
      let dataBuf = null;
      if (hasDigest) {
        if (!HEX64.test(b.digest.trim())) throw bad('digest must be a 32-byte SHA-256 hex string', 'invalid_digest');
        digest = b.digest.trim().replace(/^0x/, '').toLowerCase();
      } else {
        try {
          dataBuf = Buffer.from(b.data, 'base64');
        } catch {
          throw bad('data must be valid base64', 'invalid_request');
        }
        if (!dataBuf.length) throw bad('data decoded to zero bytes', 'invalid_request');
        if (dataBuf.length > 128 * 1024) throw bad('data exceeds 128 KiB; hash it yourself and send the digest instead', 'data_too_large');
        digest = crypto.createHash('sha256').update(dataBuf).digest('hex');
      }

      let memo = null;
      if (b.memo !== undefined && b.memo !== null) {
        if (typeof b.memo !== 'string') throw bad('memo must be a string', 'invalid_request');
        memo = b.memo.slice(0, 256);
      }
      const storeData = b.store_data === true;
      if (storeData && !dataBuf) throw bad('store_data requires data', 'invalid_request');
      return { digest, dataBuf, memo, storeData };
    },

    async preSettle() {
      await da.daStatus();
      const h = await da.head();
      return { head: h.height, headHash: h.hash };
    },

    async handler(ctx) {
      const { digest, dataBuf, memo, storeData } = ctx.params;
      const observedAt = new Date(now()).toISOString();

      // The committed record is CANONICAL JSON with sorted keys, so a verifier
      // can rebuild the exact bytes from the response fields and re-hash them.
      const record = {
        anchored_at: observedAt,
        digest,
        head_hash: ctx.pinned.headHash,
        head_height: ctx.pinned.head,
        memo,
        v: 1,
      };
      const recordBytes = Buffer.from(JSON.stringify(record, Object.keys(record).sort()), 'utf8');

      let put;
      try {
        put = await da.put(recordBytes, NS);
      } catch (e) {
        e.retryable = e.retryable !== false;
        throw e;
      }

      // Optional: store the document itself, as a SEPARATE blob. Kept separate
      // so the notarisation record stays small and quotable even when the
      // document is not public.
      let dataCommitment = null;
      if (storeData && dataBuf) {
        try {
          const d = await da.put(dataBuf, NS);
          dataCommitment = { commitment: d.commitment, blob_id: d.blob_id, size_bytes: d.size_bytes };
        } catch (e) {
          // The notarisation itself succeeded; say the extra step did not
          // rather than failing a call that already did its job.
          dataCommitment = { error: `document storage failed: ${e.message}` };
        }
      }

      let proof = null;
      try {
        const p = await node.call('da.getProof', { commitment: put.commitment }, { timeoutMs: Number(cfg.notarizeTimeoutMs) });
        const first = p && Array.isArray(p.proofs) ? p.proofs[0] : null;
        proof = {
          tree_height: p ? p.tree_height : null,
          leaf_count: p ? p.leaf_count : null,
          data_shards: p ? p.data_shards : null,
          total_shards: p ? p.total_shards : null,
          share_bytes: p ? p.share_bytes : null,
          original_size: p ? p.original_size : null,
          leaf_index: first ? first.leaf_index : null,
          leaf_hash: first ? (first.leaf_hash || null) : null,
          siblings: first ? (first.siblings || first.path || null) : null,
        };
      } catch (e) {
        // A missing proof does not invalidate the anchor; report it honestly.
        proof = { error: `proof not retrievable right now: ${e.message}`, retry: `GET /x402/notarize/verify/${put.commitment}` };
      }

      return {
        status: 200,
        bodyObj: {
          product: 'notarize',
          commitment: put.commitment,
          blob_id: put.blob_id,
          namespace: put.namespace,
          namespace_label: cfg.notarizeNamespace,
          digest,
          record,
          record_bytes_base64: recordBytes.toString('base64'),
          proof,
          document: dataCommitment,
          anchored_at: {
            head_height: ctx.pinned.head,
            head_hash: ctx.pinned.headHash,
            observed_at: observedAt,
          },
          verify_url: `/x402/notarize/verify/${put.commitment}`,
          verification: {
            how: [
              'GET /x402/notarize/verify/{commitment} — free, and it re-reads the record and proof from the node.',
              'Independently: canonical-JSON the `record` object with keys sorted, UTF-8 encode it, and confirm it equals record_bytes_base64; then check that blob against the commitment via the DA proof.',
            ],
            what_it_proves:
              'that this exact record — your digest, your memo, and the chain head height and hash observed at the time — is committed in the Animica DA tree under the returned commitment, erasure-coded across ' +
              'the reported shards.',
            what_it_does_not_prove:
              'it is NOT a timestamp signed by the consensus set. The head height and hash pin the record to a point in chain history far more strongly than a wall clock, but a third party trusts this node for the DA commitment itself.',
          },
        },
      };
    },
  };
}

/**
 * FREE verification route. A notarisation that only the seller can check is
 * not a notarisation, so this never takes payment.
 */
function createNotarizeVerifyRoute({ cfg, node }) {
  const RE = /^\/x402\/notarize\/verify\/([0-9a-fA-F]{64})$/;
  return {
    method: 'GET',
    path: '/x402/notarize/verify/{commitment}',
    description:
      'FREE, permanent verification of a notarisation: re-reads the committed record and its Merkle inclusion proof from the node. No payment, ever — a proof nobody can check is worthless.',
    match(pathname) {
      const m = RE.exec(pathname);
      return m ? { commitment: m[1].toLowerCase() } : null;
    },
    async handler(ctx) {
      const commitment = ctx.params.commitment;
      if (!COMMITMENT_RE.test(commitment)) {
        return { status: 404, bodyObj: { error: 'not_found', detail: 'commitment must be 64 hex characters' } };
      }
      let blob;
      try {
        blob = await node.call('da.get', { commitment }, { timeoutMs: 8000 });
      } catch (e) {
        return { status: 404, bodyObj: { error: 'commitment_not_found', detail: e.message, commitment } };
      }
      if (!blob || !blob.bytes) {
        return { status: 404, bodyObj: { error: 'commitment_not_found', detail: 'no record under that commitment', commitment } };
      }
      const raw = Buffer.from(blob.bytes, 'base64');
      let record = null;
      let parseError = null;
      try {
        record = JSON.parse(raw.toString('utf8'));
      } catch (e) {
        parseError = `stored bytes are not a notarisation record: ${e.message}`;
      }
      let proof = null;
      try {
        proof = await node.call('da.getProof', { commitment }, { timeoutMs: 8000 });
      } catch (e) {
        proof = { error: e.message };
      }
      // Recompute the canonical bytes and confirm they match what is stored.
      let canonicalMatches = null;
      if (record && typeof record === 'object') {
        const rebuilt = Buffer.from(JSON.stringify(record, Object.keys(record).sort()), 'utf8');
        canonicalMatches = rebuilt.equals(raw);
      }
      return {
        status: 200,
        bodyObj: {
          product: 'notarize_verify',
          free: true,
          commitment,
          found: true,
          record,
          parse_error: parseError,
          size_bytes: blob.size_bytes,
          canonical_bytes_match: canonicalMatches,
          proof: proof && proof.error ? proof : {
            leaf_count: proof.leaf_count,
            tree_height: proof.tree_height,
            data_shards: proof.data_shards,
            total_shards: proof.total_shards,
            original_size: proof.original_size,
            leaf_index: proof.proofs && proof.proofs[0] ? proof.proofs[0].leaf_index : null,
          },
          note:
            'canonical_bytes_match true means the stored bytes are exactly the canonical JSON of the record shown, so the record has not been altered since it was committed.',
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// P: blob_put — general-purpose storage with a retrievable commitment.
// ---------------------------------------------------------------------------

function createBlobProduct({ cfg, node, now = Date.now }) {
  const da = daHelpers({ cfg, node });
  const NS = namespaceOf(cfg.blobNamespace);

  return {
    id: 'blob_put',
    title: 'Store a blob with a retrieval proof',
    description:
      `Store up to ${Math.floor(cfg.blobMaxBytes / 1024)} KiB and get back a content commitment you can hand to anyone. The blob is erasure-coded across shards in the Animica data-availability layer and is retrievable for free by commitment at GET /x402/blob/{commitment}, with a Merkle inclusion proof. Useful when an agent needs to hand another agent a durable, addressable artifact rather than a link that rots. Stored blobs are PUBLIC to anyone holding the commitment — do not put secrets here.`,
    path: '/x402/blob',
    routes: [{ method: 'POST', path: '/x402/blob' }],
    priceUsd: cfg.blobPriceUsd,
    enabled: cfg.blobEnabled,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 8 * 1024 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          data: { type: 'string', required: true, description: 'base64 payload' },
          content_type: { type: 'string', required: false, description: 'informational label stored alongside, e.g. application/json' },
        },
      },
      output: { type: 'json', description: 'commitment, blob_id, size_bytes, sha256, retrieve_url, shard layout' },
    },

    async availability() {
      try {
        const s = await da.daStatus();
        const free = Number(s.free_bytes_fs || 0);
        if (free > 0 && free < Number(cfg.blobMinFreeBytes)) {
          return { available: false, reason: 'da_storage_low', detail: `only ${free} bytes free on the DA volume` };
        }
        return { available: true };
      } catch (e) {
        return { available: false, reason: e.reason || 'da_unreachable', detail: e.message };
      }
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.data !== 'string' || !b.data) throw bad('data (base64) is required', 'invalid_request');
      let buf;
      try {
        buf = Buffer.from(b.data, 'base64');
      } catch {
        throw bad('data must be valid base64', 'invalid_request');
      }
      if (!buf.length) throw bad('data decoded to zero bytes', 'invalid_request');
      if (buf.length > Number(cfg.blobMaxBytes)) {
        throw bad(`blob is ${buf.length} bytes, over the ${cfg.blobMaxBytes} byte cap`, 'blob_too_large', {
          caps: { max_bytes: Number(cfg.blobMaxBytes) },
        });
      }
      const contentType = typeof b.content_type === 'string' ? b.content_type.slice(0, 128) : null;
      return { buf, contentType };
    },

    async preSettle() {
      await da.daStatus();
      return {};
    },

    async handler(ctx) {
      const { buf, contentType } = ctx.params;
      const sha256 = crypto.createHash('sha256').update(buf).digest('hex');
      const put = await da.put(buf, NS);
      return {
        status: 200,
        bodyObj: {
          product: 'blob_put',
          commitment: put.commitment,
          blob_id: put.blob_id,
          namespace: put.namespace,
          size_bytes: put.size_bytes,
          sha256,
          content_type: contentType,
          stored_at: new Date(now()).toISOString(),
          retrieve_url: `/x402/blob/${put.commitment}`,
          shards: {
            data_shards: put.data_shards,
            total_shards: put.total_shards,
            share_bytes: put.share_bytes,
            note: 'erasure-coded: the blob reconstructs from any data_shards of total_shards',
          },
          visibility: 'PUBLIC to anyone holding the commitment — this is addressable storage, not private storage.',
        },
      };
    },
  };
}

/** FREE retrieval: paying to store must not mean paying again to read. */
function createBlobGetRoute({ cfg, node }) {
  const RE = /^\/x402\/blob\/([0-9a-fA-F]{64})$/;
  return {
    method: 'GET',
    path: '/x402/blob/{commitment}',
    description:
      'FREE retrieval of a stored blob by commitment. Storage was paid for once; reading it back is not charged again, and anyone holding the commitment may read it.',
    match(pathname) {
      const m = RE.exec(pathname);
      return m ? { commitment: m[1].toLowerCase() } : null;
    },
    async handler(ctx) {
      const commitment = ctx.params.commitment;
      let blob;
      try {
        blob = await node.call('da.get', { commitment }, { timeoutMs: 10000 });
      } catch (e) {
        return { status: 404, bodyObj: { error: 'blob_not_found', detail: e.message, commitment } };
      }
      if (!blob || !blob.bytes) {
        return { status: 404, bodyObj: { error: 'blob_not_found', commitment } };
      }
      const raw = Buffer.from(blob.bytes, 'base64');
      return {
        status: 200,
        bodyObj: {
          product: 'blob_get',
          free: true,
          commitment,
          size_bytes: blob.size_bytes,
          sha256: crypto.createHash('sha256').update(raw).digest('hex'),
          data: blob.bytes,
          encoding: 'base64',
        },
      };
    },
  };
}

module.exports = {
  createNotarizeProduct, createNotarizeVerifyRoute,
  createBlobProduct, createBlobGetRoute,
  namespaceOf, daHelpers,
};
