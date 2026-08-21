'use strict';
/**
 * Paid media rendering — image / video / audio, rendered by the GPU-miner
 * queue behind the marketplace media API (/api/mkt/v1/media).
 *
 * WHY THIS IS SOLD AS THREE PRODUCTS, NOT ONE. A 5-second video and a single
 * image are not the same amount of GPU time, and a discovery agent choosing
 * what to buy needs distinct prices to choose between. Grouping the 16
 * marketplace kinds into three cost families keeps the catalog honest without
 * pricing every kind separately.
 *
 * AVAILABILITY IS THE WHOLE DESIGN. Rendering is an OPT-IN queue: PoW miners
 * do not join it by default, and the network has spent long stretches with
 * zero renderers online for a given kind. The marketplace's own
 * /media/capabilities counts miners with `online: true` AND a heartbeat inside
 * MEDIA_MINER_STALE_SECS, so it is a live signal rather than a stored flag —
 * this product gates on it per KIND (not merely "anyOnline"), because a box
 * that can render images may advertise none of the video kinds.
 *
 * Same rule as priority inference, for the same reason: NEVER take payment for
 * a service known unavailable. The gate is re-probed synchronously in
 * preSettle(), because the renderer for a kind can drop between the 402 and
 * the paid retry, and refusing at that moment costs the payer nothing.
 *
 * ASYNC BY NATURE. A render is queued, claimed by a miner, and delivered
 * later — it does not fit in one HTTP response. The paid call therefore
 * returns the marketplace's 202 handle ({job_id, poll_url, position, ...}),
 * and POLLING IS FREE on the marketplace's own public endpoint. Charging per
 * poll would bill a caller repeatedly for one render, and long-polling here
 * would hold a paid connection open for minutes against a queue whose lease
 * semantics already caused one delivery incident.
 *
 * WHAT THE PAID LANE ADDS over submitting to the free public queue: admission
 * is gated on a renderer for that kind being online right now (the free path
 * accepts a job into an empty queue and leaves it sitting), and the paid
 * submission carries a priority marker for the queue. It does NOT reserve a
 * specific miner or guarantee a deadline — the same renderers serve both
 * lanes, and saying otherwise in the catalog would be a lie an agent could
 * act on.
 */

const { ProductError } = require('./errors');

/**
 * Cost families. Keys are the marketplace's canonical kinds (lib/mediaQueue
 * SUBMIT_KINDS); the marketplace also accepts friendlier aliases ("video",
 * "music", "upscale"), which we normalize here so the paid API and the free
 * one take the same words.
 */
const FAMILIES = {
  image: {
    id: 'media_image',
    title: 'AI image render',
    kinds: ['image'],
    priceKey: 'mediaImagePriceUsd',
    blurb: 'Text-to-image, rendered by an online GPU miner.',
  },
  video: {
    id: 'media_video',
    title: 'AI video render',
    kinds: [
      'video_t2v', 'video_i2v', 'video_multiscene', 'video_upscale',
      'video_interpolate', 'video_subtitles', 'video_bgremove', 'video_shorts',
    ],
    priceKey: 'mediaVideoPriceUsd',
    blurb: 'Text-to-video, image-to-video, upscale, interpolate, subtitles, background removal and shorts.',
  },
  audio: {
    id: 'media_audio',
    title: 'AI audio render',
    kinds: ['audio', 'audio_stems', 'audio_isolate', 'audio_enhance', 'audio_master'],
    priceKey: 'mediaAudioPriceUsd',
    blurb: 'Music generation, stem separation, vocal isolation, enhancement and mastering.',
  },
};

/** Marketplace aliases -> canonical kind, mirroring normalizeKind() there. */
const ALIASES = {
  video: 'video_t2v', i2v: 'video_i2v', multiscene: 'video_multiscene', scenes: 'video_multiscene',
  music: 'audio', song: 'audio',
  upscale: 'video_upscale', interpolate: 'video_interpolate', smooth: 'video_interpolate',
  subtitles: 'video_subtitles', captions: 'video_subtitles',
  bgremove: 'video_bgremove', greenscreen: 'video_bgremove',
  shorts: 'video_shorts',
  stems: 'audio_stems', isolate: 'audio_isolate', vocals: 'audio_isolate',
  enhance: 'audio_enhance', master: 'audio_master',
  // Documented/natural names the catalog + llms.txt advertise, so an agent that
  // copies a kind straight from the docs resolves to a canonical kind instead of
  // getting wrong_product. canonicalKind() normalizes spaces/underscores to the
  // hyphen form used by these keys before lookup.
  'text-to-image': 'image', t2i: 'image', txt2img: 'image', img: 'image', picture: 'image',
  'text-to-video': 'video_t2v', t2v: 'video_t2v', txt2vid: 'video_t2v',
  'image-to-video': 'video_i2v', img2vid: 'video_i2v',
  'background-removal': 'video_bgremove', 'remove-background': 'video_bgremove', 'bg-remove': 'video_bgremove',
  'music-generation': 'audio', 'text-to-music': 'audio', 'text-to-audio': 'audio', 'generate-music': 'audio',
  'stem-separation': 'audio_stems', 'separate-stems': 'audio_stems',
  'vocal-isolation': 'audio_isolate', 'isolate-vocals': 'audio_isolate', 'vocal-removal': 'audio_isolate',
  enhancement: 'audio_enhance', 'audio-enhance': 'audio_enhance',
  mastering: 'audio_master', 'audio-master': 'audio_master',
};

function canonicalKind(raw) {
  const k = String(raw || '').trim().toLowerCase();
  if (!k) return '';
  if (ALIASES[k]) return ALIASES[k];
  // Try the alias-table form (spaces/underscores -> hyphens): "text to image",
  // "text_to_image" and "text-to-image" all resolve the same.
  const hy = k.replace(/[\s_]+/g, '-');
  if (ALIASES[hy]) return ALIASES[hy];
  // Fall back to the canonical underscore form, so a hyphenated canonical name
  // ("video-i2v") still matches family.kinds ("video_i2v").
  return k.replace(/[\s-]+/g, '_');
}

/**
 * Live per-kind renderer counts from the marketplace. Fails CLOSED: any error
 * (unreachable, non-200, malformed) yields zeros, so the product reports
 * unavailable rather than selling a render nothing can serve.
 */
function createMediaCapacity({ cfg, fetchImpl = fetch, now = Date.now }) {
  let counts = {};
  let at = 0;
  let lastError = null;

  async function probeOnce() {
    const res = await fetchImpl(cfg.mediaCapabilitiesUrl, {
      method: 'GET',
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(cfg.mediaProbeTimeoutMs),
    });
    if (!res.ok) throw new Error(`capabilities ${res.status}`);
    const body = await res.json();
    const kinds = body && typeof body.kinds === 'object' && body.kinds ? body.kinds : null;
    if (!kinds) throw new Error('capabilities payload missing kinds');
    counts = kinds;
    at = now();
    lastError = null;
    return counts;
  }

  return {
    probeOnce,
    /** Renderers online for a canonical kind; 0 when unknown or stale. */
    onlineFor(kind) {
      if (!at || now() - at > cfg.mediaMaxProbeAgeMs) return 0;
      const n = counts[kind];
      return Number.isFinite(n) ? n : 0;
    },
    snapshot(kind) {
      return {
        kind,
        online: this.onlineFor(kind),
        required: cfg.mediaMinRenderers,
        probe_age_ms: at ? now() - at : null,
        last_error: lastError,
      };
    },
    async safeProbe() {
      try {
        await probeOnce();
      } catch (e) {
        lastError = e && e.message ? e.message : String(e);
        // Do NOT clear counts here — `at` ageing out is what makes the gate
        // close. Clearing on a single transient blip would flap the catalog.
      }
    },
  };
}

function createMediaProduct({ cfg, family, capacity, fetchImpl = fetch }) {
  const price = cfg[family.priceKey];
  const kindList = family.kinds.join(', ');

  /** Best online count across the family — what the CATALOG advertises. */
  function bestOnline() {
    return family.kinds.reduce((m, k) => Math.max(m, capacity.onlineFor(k)), 0);
  }

  function gateBody(kind) {
    const snap = capacity.snapshot(kind || family.kinds[0]);
    return {
      error: 'media_renderer_unavailable',
      family: family.id,
      kind: snap.kind,
      renderers_online: snap.online,
      required: snap.required,
      detail: kind
        ? `no GPU miner is online for kind "${kind}" right now (${snap.online}/${snap.required})`
        : `no GPU miner is online for any ${family.id.replace('media_', '')} kind right now`,
    };
  }

  return {
    id: family.id,
    title: family.title,
    description:
      `${family.blurb} Rendered by the Animica GPU-miner queue. Sold only while a miner ` +
      `for the requested kind is online — the catalog reports available:false and the ` +
      `endpoint answers 503 otherwise. Returns a job handle immediately (rendering is ` +
      `asynchronous); polling the returned poll_url is free. Kinds: ${kindList}.`,
    path: `/x402/media/${family.id.replace('media_', '')}`,
    routes: [{ method: 'POST', path: `/x402/media/${family.id.replace('media_', '')}` }],
    priceUsd: price,
    enabled: cfg.mediaEnabled,
    // The honest available:false IS the product surface here — an agent needs
    // to see that this exists and is currently unserved, not a 404.
    listedEvenWhenUnavailable: true,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: cfg.mediaMaxBodyBytes,
    injectPayment: false,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          kind: { type: 'string', description: `one of: ${kindList} (aliases accepted, e.g. "video", "music", "upscale")` },
          prompt: { type: 'string', description: 'what to render; required except for upload-driven kinds' },
          params: { type: 'object', description: 'optional per-kind parameters, clamped by the renderer' },
          upload_ids: { type: 'array', description: 'ids from /api/mkt/v1/media/uploads, for image-to-video and the edit kinds' },
        },
      },
      output: {
        type: 'json',
        description:
          'Job handle: {job_id, kind, status, position, miners_online, poll_url, message}. ' +
          'Rendering is asynchronous — GET poll_url (free, public) until status is done, then fetch the artifact.',
      },
    },

    async availability() {
      if (!cfg.mediaEnabled) {
        return {
          available: false,
          reason: 'media_disabled',
          detail: 'paid media rendering is disabled by the operator (X402_MEDIA_ENABLED=0)',
          body: gateBody(null),
        };
      }
      const online = bestOnline();
      if (online >= cfg.mediaMinRenderers) {
        return { available: true, renderers_online: online, kinds: family.kinds };
      }
      return {
        available: false,
        reason: 'no_renderer_online',
        detail: `no GPU miner is online for any ${family.id.replace('media_', '')} kind (best ${online}/${cfg.mediaMinRenderers})`,
        body: gateBody(null),
      };
    },

    validate(ctx) {
      if (!ctx.json || typeof ctx.json !== 'object' || Array.isArray(ctx.json)) {
        throw new ProductError('body must be a JSON object', {
          body: { error: 'invalid_request', detail: 'body must be a JSON object' },
        });
      }
      const kind = canonicalKind(ctx.json.kind);
      if (!kind) {
        throw new ProductError('kind is required', {
          body: { error: 'invalid_request', detail: `kind is required; one of: ${kindList}` },
        });
      }
      if (!family.kinds.includes(kind)) {
        // Point at the right product rather than silently rendering something
        // the caller did not pay for.
        const other = Object.values(FAMILIES).find((f) => f.kinds.includes(kind));
        throw new ProductError('kind not in this product', {
          body: {
            error: 'wrong_product',
            detail: other
              ? `kind "${kind}" is sold as ${other.id} at /x402/media/${other.id.replace('media_', '')}`
              : `kind "${kind}" is not one of: ${kindList}`,
          },
        });
      }
      const hasUploads = Array.isArray(ctx.json.upload_ids) && ctx.json.upload_ids.length > 0;
      const prompt = typeof ctx.json.prompt === 'string' ? ctx.json.prompt.trim() : '';
      if (!prompt && !hasUploads) {
        throw new ProductError('prompt is required', {
          body: { error: 'invalid_request', detail: 'a prompt is required (or upload_ids for the upload-driven kinds)' },
        });
      }
      return { kind };
    },

    /**
     * MANDATORY synchronous re-probe before the payer's USDC moves. The queue
     * is opt-in and a single renderer dropping takes a kind to zero, which can
     * easily happen inside the cached probe window.
     */
    async preSettle(ctx) {
      const kind = canonicalKind(ctx && ctx.json && ctx.json.kind) || family.kinds[0];
      try {
        await capacity.probeOnce();
      } catch (e) {
        const err = new ProductError('media capability probe failed', { status: 503, body: gateBody(kind) });
        err.unavailable = true;
        throw err;
      }
      if (capacity.onlineFor(kind) < cfg.mediaMinRenderers) {
        const err = new ProductError('no renderer online for this kind', { status: 503, body: gateBody(kind) });
        err.unavailable = true;
        throw err;
      }
      return { media: capacity.snapshot(kind) };
    },

    async handler(ctx) {
      const kind = canonicalKind(ctx.json.kind);
      const payload = {
        kind,
        prompt: typeof ctx.json.prompt === 'string' ? ctx.json.prompt : '',
        params: ctx.json.params && typeof ctx.json.params === 'object' ? ctx.json.params : {},
      };
      if (Array.isArray(ctx.json.upload_ids)) payload.upload_ids = ctx.json.upload_ids;

      let res;
      try {
        res = await fetchImpl(cfg.mediaSubmitUrl, {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-animica-priority': 'x402-paid',
            'x-request-id': ctx.requestId || '',
          },
          body: JSON.stringify(payload),
          signal: AbortSignal.timeout(cfg.mediaSubmitTimeoutMs),
        });
      } catch (e) {
        const err = new Error(`media queue unreachable: ${e.message}`);
        err.retryable = true;
        throw err;
      }

      const text = await res.text();
      if (res.status >= 500) {
        const err = new Error(`media queue ${res.status}`);
        err.retryable = true;
        throw err;
      }

      // Rewrite the relative poll_url to an absolute one: the payer is an
      // agent talking to the x402 host, and a bare path would resolve against
      // the wrong origin.
      let body = text;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.poll_url === 'string' && parsed.poll_url.startsWith('/')) {
          parsed.poll_url = cfg.mediaPublicBase.replace(/\/+$/, '') + parsed.poll_url;
          parsed.poll_is_free = true;
          body = JSON.stringify(parsed);
        }
      } catch {
        // Non-JSON from the queue is passed through untouched rather than guessed at.
      }

      return {
        status: res.status,
        headers: { 'content-type': 'application/json' },
        body,
      };
    },
  };
}

function createMediaProducts({ cfg, fetchImpl = fetch, now = Date.now }) {
  const capacity = createMediaCapacity({ cfg, fetchImpl, now });
  const products = Object.values(FAMILIES).map((family) =>
    createMediaProduct({ cfg, family, capacity, fetchImpl }));
  return { products, capacity };
}

module.exports = { createMediaProducts, createMediaCapacity, FAMILIES, canonicalKind };
