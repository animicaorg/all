'use strict';
/**
 * GET /x402/openapi.json — OpenAPI 3.1 for the paid routes.
 *
 * GENERATED from the live product registry: every path, method, price and
 * availability flag below comes from the same product objects the paywall
 * charges against, so the document cannot describe an endpoint that does not
 * exist or quote a price nobody charges. Response schemas are hand-written
 * from REAL captured responses (src/discovery/samples.js) — the examples in
 * this document ARE those captures.
 *
 * The security model is documented honestly: there is no API key and no
 * bearer token. The only credential is a payment authorization the caller
 * signs locally, and the 402 challenge is a first-class documented response
 * on every paid operation (with its `payment-required` header), not a hidden
 * error case. Vendor extensions carry the machine-readable payment facts:
 *
 *   x-payment-protocol: "x402"           (document + every paid operation)
 *   x-payment-info:     {product, price, currency, amount_atomic, network,
 *                        chain_id, asset, pay_to, scheme, available,
 *                        documentation, …}   (every paid operation)
 */

const { SAMPLES, CAPTURE_NOTE } = require('./samples');
const { links, identity, networkFacts, anchorFor } = require('./links');

const PKG_VERSION = require('../../package.json').version;

/** Product id -> OpenAPI tag. Unknown products fall back to "products". */
const TAGS = {
  qrng: 'randomness',
  random_int: 'randomness',
  random_shuffle: 'randomness',
  random_pick: 'randomness',
  random_bulk: 'randomness',
  random_commit: 'randomness',
  bulk_chain: 'chain-data',
  chain_address_history: 'chain-data',
  chain_batch_balances: 'chain-data',
  priority_inference: 'inference',
};

/** JSON-schema-ish type for a declared input field; strings by default. */
function fieldSchema(spec) {
  const t = (spec && spec.type) || 'string';
  const map = {
    integer: { type: 'integer' },
    number: { type: 'number' },
    boolean: { type: 'boolean' },
    array: { type: 'array', items: {} },
    object: { type: 'object' },
    string: { type: 'string' },
  };
  const base = map[t] || { type: 'string' };
  return spec && spec.description ? Object.assign({}, base, { description: spec.description }) : base;
}

function queryParameters(input) {
  const params = (input && input.queryParams) || {};
  return Object.entries(params).map(([name, spec]) => ({
    name,
    in: 'query',
    required: Boolean(spec && spec.required),
    description: (spec && spec.description) || undefined,
    schema: fieldSchema(spec),
  }));
}

function requestBodyFrom(input, sample) {
  const fields = (input && input.bodyFields) || {};
  if (!Object.keys(fields).length) return undefined;
  const properties = {};
  const required = [];
  for (const [name, spec] of Object.entries(fields)) {
    properties[name] = fieldSchema(spec);
    if (spec && spec.required) required.push(name);
  }
  const schema = { type: 'object', properties, additionalProperties: false };
  if (required.length) schema.required = required;
  const content = { schema };
  if (sample && sample.body) content.example = sample.body;
  return { required: required.length > 0, content: { 'application/json': content } };
}

/**
 * The success schema for a product. Known products get a real shape derived
 * from their captured response; anything else gets an honest generic object
 * rather than an invented schema.
 */
function successSchema(productId) {
  switch (productId) {
    case 'qrng':
      return { $ref: '#/components/schemas/RandomBytesDraw' };
    case 'random_int':
    case 'random_shuffle':
    case 'random_pick':
      return { $ref: '#/components/schemas/DerivedRandomness' };
    case 'random_bulk':
      return { $ref: '#/components/schemas/BulkRandomness' };
    case 'random_commit':
      return { $ref: '#/components/schemas/RandomCommitment' };
    case 'bulk_chain':
      return { $ref: '#/components/schemas/ChainExport' };
    case 'chain_address_history':
      return { $ref: '#/components/schemas/AddressHistory' };
    case 'chain_batch_balances':
      return { $ref: '#/components/schemas/BatchBalances' };
    case 'priority_inference':
      return { $ref: '#/components/schemas/ChatCompletion' };
    default:
      return { type: 'object', description: 'product-specific JSON payload plus a payment block' };
  }
}

function baseSchemas() {
  return {
    Error: {
      type: 'object',
      required: ['error'],
      properties: {
        error: { type: 'string', description: 'stable machine-readable error code' },
        detail: { type: 'string' },
      },
      additionalProperties: true,
    },
    PaymentRequirements: {
      type: 'object',
      description: 'One set of terms the server will accept, verbatim. Amounts are integer atomic units of the asset as a decimal string — never floating point.',
      required: ['scheme', 'network', 'amount', 'asset', 'payTo', 'maxTimeoutSeconds'],
      properties: {
        scheme: { type: 'string', examples: ['exact'] },
        network: { type: 'string', description: 'CAIP-2 chain id', examples: ['eip155:8453'] },
        amount: { type: 'string', pattern: '^\\d+$', description: 'atomic units of `asset`' },
        asset: { type: 'string', description: 'token contract address' },
        payTo: { type: 'string', description: 'recipient; server configuration only, never client-supplied' },
        maxTimeoutSeconds: { type: 'integer' },
        extra: { type: 'object', additionalProperties: true },
      },
    },
    PaymentRequired: {
      type: 'object',
      description: 'The x402 v2 challenge object. Sent base64-encoded in the `payment-required` response header; the JSON body of the same 402 carries the v1 rendering for older clients.',
      required: ['x402Version', 'resource', 'accepts'],
      properties: {
        x402Version: { type: 'integer', examples: [2] },
        resource: {
          type: 'object',
          required: ['url'],
          properties: {
            url: { type: 'string', format: 'uri' },
            description: { type: 'string' },
            mimeType: { type: 'string' },
            serviceName: { type: 'string' },
          },
        },
        accepts: { type: 'array', items: { $ref: '#/components/schemas/PaymentRequirements' } },
        extensions: {
          type: 'object',
          description: 'Open discovery metadata. `discovery.info` carries the input/output schema indexers read, and is the key to build against; `bazaar` is a byte-identical compatibility alias for indexers that still key on that name. `animica` carries the descriptive product facts (id, name, price, documentation URL).',
          additionalProperties: true,
        },
        error: { type: 'string' },
      },
    },
    PaymentRequiredV1Body: {
      type: 'object',
      description: 'v1 rendering of the same offer, sent as the 402 body.',
      required: ['x402Version', 'accepts'],
      properties: {
        x402Version: { type: 'integer', examples: [1] },
        error: { type: 'string' },
        accepts: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              scheme: { type: 'string' },
              network: { type: 'string', description: 'v1 network slug', examples: ['base'] },
              maxAmountRequired: { type: 'string' },
              asset: { type: 'string' },
              payTo: { type: 'string' },
              resource: { type: 'string', format: 'uri' },
              description: { type: 'string' },
              mimeType: { type: 'string' },
              outputSchema: { type: ['object', 'null'], additionalProperties: true },
              maxTimeoutSeconds: { type: 'integer' },
            },
          },
        },
      },
    },
    SettlementResponse: {
      type: 'object',
      description: 'Sent base64-encoded in the `payment-response` header of a delivered paid response.',
      required: ['success', 'transaction', 'network'],
      properties: {
        success: { type: 'boolean' },
        transaction: { type: 'string', description: 'settlement transaction hash ("" when nothing settled)' },
        network: { type: 'string' },
        payer: { type: 'string' },
        amount: { type: 'string' },
        errorReason: { type: 'string' },
      },
    },
    PaymentMetadata: {
      type: 'object',
      description: 'Injected into every paid JSON body (except priority inference, whose body stays a pure OpenAI object — its proof rides in the headers).',
      properties: {
        network: { type: 'string' },
        asset: { type: 'string' },
        amount_atomic: { type: 'string' },
        price_usd: { type: 'string' },
        payer: { type: 'string' },
        settlement_tx: { type: 'string' },
      },
    },
    EntropySource: {
      type: 'object',
      description: 'The node\'s own description of where the bytes came from, passed through verbatim.',
      properties: {
        name: { type: 'string' },
        vendor: { type: 'string' },
        model: { type: 'string' },
        is_hardware: { type: 'boolean' },
        is_quantum: { type: 'boolean' },
        device_path: { type: ['string', 'null'] },
        attested: { type: 'boolean' },
        notes: { type: 'string' },
      },
    },
    EntropyHealth: {
      type: 'object',
      description: 'SP 800-90B-style health report. The gateway refuses to sell a draw whose `passed` is false (503, no payment requested).',
      properties: {
        passed: { type: 'boolean' },
        min_entropy_per_byte: { type: 'number' },
      },
    },
    Attestation: {
      type: 'object',
      description: 'ed25519 signature by the serving node over sha3-256 of the returned bytes. `attested` is true only when the signer is hardware-backed; it is false on a software signer, which is the case today.',
      properties: {
        alg: { type: 'string', examples: ['ed25519'] },
        backend: { type: 'string', examples: ['software'] },
        attested: { type: 'boolean' },
        public_key_hex: { type: 'string' },
        digest_hex: { type: 'string' },
        signature_hex: { type: 'string' },
      },
    },
    Verification: {
      type: 'object',
      description: 'The exact rules to check the response yourself.',
      properties: {
        method: { type: 'string' },
        rules: { type: 'array', items: { type: 'string' } },
        verifier: { type: 'string' },
        trust_model: { type: 'string' },
        attested: { type: 'boolean' },
      },
    },
    RandomBytesDraw: {
      type: 'object',
      description: 'Signed random bytes from the Animica node randomness service.',
      properties: {
        product: { type: 'string', const: 'qrng' },
        randomness: { type: 'string', description: 'hex-encoded bytes' },
        encoding: { type: 'string', const: 'hex' },
        bytes: { type: 'integer' },
        source: { $ref: '#/components/schemas/EntropySource' },
        health: { $ref: '#/components/schemas/EntropyHealth' },
        attestation: { $ref: '#/components/schemas/Attestation' },
        verification: { $ref: '#/components/schemas/Verification' },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    Derivation: {
      type: 'object',
      description: 'Everything needed to recompute a derived result from the published draw: domain string, seed, rules, and a `recompute` object the repo verifier accepts as-is.',
      properties: {
        algorithm: { type: 'string' },
        kind: { type: 'string' },
        request_id: { type: 'string' },
        domain: { type: 'string' },
        seed_hex: { type: 'string' },
        entropy_hex: { type: 'string' },
        rules: { type: 'object', additionalProperties: { type: 'string' } },
        steps: { type: 'array', items: { type: 'string' } },
        stream_bytes_consumed: { type: 'integer' },
        recompute: { type: 'object', additionalProperties: true },
        verifier: { type: 'object', additionalProperties: true },
      },
    },
    DerivedRandomness: {
      type: 'object',
      description: 'A result derived deterministically from one signed draw. `result` shape depends on the product (ints / permutation+items / indices+picked).',
      properties: {
        product: { type: 'string' },
        result: { type: 'object', additionalProperties: true },
        randomness: { type: 'string' },
        encoding: { type: 'string' },
        bytes: { type: 'integer' },
        source: { $ref: '#/components/schemas/EntropySource' },
        health: { $ref: '#/components/schemas/EntropyHealth' },
        attestation: { $ref: '#/components/schemas/Attestation' },
        verification: { $ref: '#/components/schemas/Verification' },
        derivation: { $ref: '#/components/schemas/Derivation' },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    BulkRandomness: {
      type: 'object',
      description: 'N independent draws, each with its own source/health/attestation, settled once.',
      properties: {
        product: { type: 'string', const: 'random_bulk' },
        draws: { type: 'array', items: { type: 'object', additionalProperties: true } },
        count: { type: 'integer' },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    RandomCommitment: {
      type: 'object',
      description: 'The sealed half of a commit-reveal. The draw itself is NOT disclosed until the free reveal endpoint discloses it.',
      properties: {
        product: { type: 'string', const: 'random_commit' },
        stage: { type: 'string', const: 'commit' },
        commit_id: { type: 'string' },
        commitment: { type: 'string' },
        algorithm: { type: 'string' },
        reveal_after: { type: 'integer', description: 'unix seconds' },
        reveal_url: { type: 'string', format: 'uri' },
        reveal_is_free: { type: 'boolean', const: true },
        source: { $ref: '#/components/schemas/EntropySource' },
        health: { $ref: '#/components/schemas/EntropyHealth' },
        attestation: { $ref: '#/components/schemas/Attestation' },
        verification: { $ref: '#/components/schemas/Verification' },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    RandomReveal: {
      type: 'object',
      description: 'The free disclosure of a commitment: secret, salt and the raw draw, so anyone can check commitment == sha3_256(secret || salt).',
      additionalProperties: true,
      properties: {
        stage: { type: 'string', const: 'reveal' },
        commit_id: { type: 'string' },
        commitment: { type: 'string' },
        secret_hex: { type: 'string' },
        salt_hex: { type: 'string' },
      },
    },
    ChainExport: {
      type: 'object',
      description: 'Block/transaction range export. `format=ndjson|csv` returns the same records in those encodings instead.',
      properties: {
        meta: {
          type: 'object',
          description: 'export descriptor: range, head height, chain id, unit, payment block',
          additionalProperties: true,
        },
        blocks: { type: 'array', items: { type: 'object', additionalProperties: true } },
        transactions: { type: 'array', items: { type: 'object', additionalProperties: true } },
      },
    },
    AddressHistory: {
      type: 'object',
      description: 'Transactions touching an account, newest first, from the gateway\'s own address index.',
      additionalProperties: true,
      properties: {
        product: { type: 'string', const: 'chain_address_history' },
        address: { type: 'string' },
        transactions: { type: 'array', items: { type: 'object', additionalProperties: true } },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    BatchBalances: {
      type: 'object',
      description: 'Confirmed balances for many accounts in one call, pinned to a head height. Amounts are exact decimal strings in nANM (1 ANM = 10^9 nANM).',
      properties: {
        product: { type: 'string', const: 'chain_batch_balances' },
        unit: { type: 'string', const: 'nANM' },
        decimals: { type: 'integer', const: 9 },
        count: { type: 'integer' },
        unique_addresses: { type: 'integer' },
        failed_lookups: { type: 'integer' },
        total_balance: { type: 'string' },
        as_of: { type: 'object', additionalProperties: true },
        balances: { type: 'array', items: { type: 'object', additionalProperties: true } },
        derivation: { type: 'object', additionalProperties: true },
        payment: { $ref: '#/components/schemas/PaymentMetadata' },
      },
    },
    ChatCompletion: {
      type: 'object',
      description: 'OpenAI-compatible chat.completion object, passed through from the Animica serving bridge unchanged. Settlement details ride in the `payment-response` header, not in the body.',
      additionalProperties: true,
      properties: {
        id: { type: 'string' },
        object: { type: 'string', examples: ['chat.completion'] },
        model: { type: 'string' },
        choices: { type: 'array', items: { type: 'object', additionalProperties: true } },
        usage: { type: 'object', additionalProperties: true },
      },
    },
    Catalog: {
      type: 'object',
      description: 'The machine catalog: identity, network/asset facts and every product with its live availability.',
      additionalProperties: true,
      properties: {
        name: { type: 'string' },
        provider: { type: 'string' },
        network: { type: ['string', 'null'] },
        chain_id: { type: ['integer', 'null'] },
        asset: { type: ['string', 'null'] },
        payment_protocol: { type: 'string', const: 'x402' },
        products: { type: 'array', items: { type: 'object', additionalProperties: true } },
      },
    },
    Stats: {
      type: 'object',
      description: 'Aggregate settlement counts. Deliberately carries no payer addresses, no transaction hashes and no per-payment rows.',
      additionalProperties: true,
      properties: {
        available: { type: 'boolean' },
        settlements: { type: ['object', 'null'], additionalProperties: true },
        products: { type: 'array', items: { type: 'object', additionalProperties: true } },
      },
    },
    ErrorReceipt: {
      type: 'object',
      description: 'Signed, machine-readable evidence that a payment settled but the service then failed. Keep it: it references the settled payment and entitles reconciliation.',
      additionalProperties: true,
      properties: {
        error: { type: 'string' },
        incident_id: { type: 'string' },
        settlement_tx: { type: ['string', 'null'] },
        receipt: { type: 'object', additionalProperties: true },
      },
    },
  };
}

function baseComponents() {
  return {
    schemas: baseSchemas(),
    headers: {
      PaymentRequiredHeader: {
        description: 'base64-encoded PaymentRequired (x402 v2). The 402 body carries the v1 rendering of the same offer.',
        required: true,
        schema: { type: 'string', contentEncoding: 'base64', contentMediaType: 'application/json' },
      },
      PaymentResponseHeader: {
        description: 'base64-encoded SettlementResponse: the on-chain proof that this response was paid for.',
        schema: { type: 'string', contentEncoding: 'base64', contentMediaType: 'application/json' },
      },
      RequestIdHeader: {
        description: 'The x-request-id you sent, or the one this gateway assigned. Quote it in support/reconciliation.',
        schema: { type: 'string' },
      },
    },
    parameters: {
      PaymentSignature: {
        name: 'payment-signature',
        in: 'header',
        required: false,
        description: 'base64-encoded PaymentPayload (x402 v2) signed by the caller. Absent => the endpoint answers 402 with the terms to sign. v1 clients use `x-payment` instead.',
        schema: { type: 'string' },
      },
      XPayment: {
        name: 'x-payment',
        in: 'header',
        required: false,
        description: 'x402 v1 payment header. Accepted for backwards compatibility; the settlement proof then also comes back in `x-payment-response`.',
        schema: { type: 'string' },
      },
      IdempotencyKey: {
        name: 'idempotency-key',
        in: 'header',
        required: false,
        description: 'Retry-safety: the same key with the same payment replays the stored result instead of charging again.',
        schema: { type: 'string', maxLength: 200 },
      },
    },
    responses: {
      PaymentRequired: {
        description: 'Payment required. The terms are in the `payment-required` header (v2) and in the body (v1). Sign them locally and retry — nothing has been charged.',
        headers: { 'payment-required': { $ref: '#/components/headers/PaymentRequiredHeader' } },
        content: {
          'application/json': { schema: { $ref: '#/components/schemas/PaymentRequiredV1Body' } },
        },
      },
      InvalidRequest: {
        description: 'The request parameters are invalid. Checked BEFORE any payment is requested, so nothing was charged.',
        content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
      },
      Unavailable: {
        description: 'The product is currently unavailable (backend unhealthy, capacity below floor, index stale). No 402 is emitted and no payment is requested — this service is never sold while it is known to be unavailable.',
        content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
      },
      PaidServiceFailed: {
        description: 'The payment settled but the response could not be delivered, or the settlement outcome could not be confirmed. Carries a SIGNED error receipt referencing the payment plus an incident id for reconciliation.',
        content: { 'application/json': { schema: { $ref: '#/components/schemas/ErrorReceipt' } } },
      },
    },
  };
}

function paidOperation({ product, entry, route, facts, l, tag }) {
  const input = (product.outputSchema && product.outputSchema.input) || {};
  const sample = SAMPLES[product.id];
  const isPost = route.method === 'POST';
  const parameters = [
    ...(route.method === 'GET' ? queryParameters(input) : []),
    { $ref: '#/components/parameters/PaymentSignature' },
    { $ref: '#/components/parameters/XPayment' },
    { $ref: '#/components/parameters/IdempotencyKey' },
  ];
  const alternate = (input.alternateMethods || []).find((a) => a.method === route.method);
  const bodyInput = isPost ? (alternate || input) : null;
  const requestBody = isPost ? requestBodyFrom(bodyInput, sample) : undefined;

  const okContent = { schema: successSchema(product.id) };
  if (sample && sample.response && (!sample.status || sample.status === 200)) {
    okContent.examples = {
      captured: {
        summary: `real response captured from this endpoint (${sample.method} ${sample.path})`,
        description: CAPTURE_NOTE,
        value: sample.response,
      },
    };
  }

  const responses = {
    200: {
      description: 'Paid and delivered. The settlement proof is in the `payment-response` header.',
      headers: {
        'payment-response': { $ref: '#/components/headers/PaymentResponseHeader' },
        'x-request-id': { $ref: '#/components/headers/RequestIdHeader' },
      },
      content: { [product.mimeType || 'application/json']: okContent },
    },
    400: { $ref: '#/components/responses/InvalidRequest' },
    402: { $ref: '#/components/responses/PaymentRequired' },
    502: { $ref: '#/components/responses/PaidServiceFailed' },
    503: { $ref: '#/components/responses/Unavailable' },
  };
  if (product.id === 'priority_inference' && sample && sample.status === 503) {
    responses[503] = {
      description: 'Serving capacity is below the configured floor (or priority inference is disabled). No payment is requested.',
      content: {
        'application/json': {
          schema: { $ref: '#/components/schemas/Error' },
          examples: { captured: { summary: 'real 503 captured on 2026-08-15 (0 serving workers)', value: sample.response } },
        },
      },
    };
  }

  const op = {
    operationId: `${product.id}_${route.method.toLowerCase()}_${route.path.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '')}`,
    summary: entry.name,
    description: `${entry.description}\n\nPayment: $${entry.price} ${entry.currency} per request (${entry.price_atomic} atomic units) over x402. An unpaid call returns 402 with the terms; sign them locally and retry. There is no API key.`,
    tags: [tag],
    parameters,
    responses,
    externalDocs: { description: 'product documentation', url: l.docFor(product.id) },
    'x-payment-protocol': 'x402',
    'x-payment-info': {
      product: product.id,
      name: entry.name,
      price: entry.price,
      currency: entry.currency,
      amount_atomic: entry.price_atomic,
      scheme: facts.scheme,
      network: facts.network,
      network_caip2: facts.network_caip2,
      chain_id: facts.chain_id,
      asset: facts.asset,
      asset_address: facts.asset_address,
      pay_to: facts.pay_to,
      x402_version: facts.x402_version,
      available: entry.available,
      unavailable_reason: entry.unavailable_reason,
      settlement: product.mode === 'settle-then-execute'
        ? 'readiness re-checked, then settled, then executed (retry + signed receipt on downstream failure)'
        : 'produced first, then settled: a failure before settlement charges nothing',
      documentation: l.docFor(product.id),
    },
  };
  if (requestBody) op.requestBody = requestBody;
  return op;
}

function freeOperation({ product, route, l }) {
  const templated = route.path.replace(/\{([a-z_]+)\}/gi, '{$1}');
  const params = [...templated.matchAll(/\{([a-z_]+)\}/gi)].map((m) => ({
    name: m[1],
    in: 'path',
    required: true,
    schema: { type: 'string' },
  }));
  return {
    path: templated,
    method: route.method.toLowerCase(),
    op: {
      // Include the PATH, not just the method: a product may expose more than
      // one free route (forecast has both a record lookup and its published
      // calibration) and `<id>_free_get` collides between them, producing an
      // OpenAPI document with duplicate operationIds that generators reject.
      operationId: `${product.id}_free_${route.method.toLowerCase()}_${route.path
        .replace(/\{[^}]*\}/g, '')
        .replace(/[^a-z0-9]+/gi, '_')
        .replace(/^_|_$/g, '')}`,
      // A route may name itself. Falling back to the PARENT product's title is
      // right for a product's own disclosure route (random_commit's reveal) and
      // wrong the moment a product carries free routes that are their own
      // thing: every Paid Crawl endpoint was published to the directories as
      // "Crawl pass (small) — free disclosure", because they all hang off the
      // pass product. The summary is what an indexer shows a buyer, so a route
      // that knows its own name gets to use it.
      summary: route.title || `${product.title || product.id} — free disclosure`,
      description: `${route.description || 'Free, unpaid endpoint.'}\n\nNo payment is ever requested here.`,
      tags: [TAGS[product.id] || 'products'],
      parameters: params,
      // A free POST route takes a body like any other, and publishing no schema
      // for it is the same defect the paid side already fixed: an agent reading
      // this document cannot call the endpoint at all. Five live Paid Crawl
      // routes — the whole operator-onboarding path — were undocumented here.
      // Emitted from the route's own declaration, so it cannot invent fields.
      ...(route.bodyFields ? { requestBody: requestBodyFrom({ bodyFields: route.bodyFields }) } : {}),
      // The 200 body and the reveal-specific 425 belong to random_commit, not
      // to free routes in general — emitting them everywhere told indexers that
      // /x402/crawl/decide answers with a RandomReveal and can be "still
      // sealed". Opt in with `revealSemantics: true`; everything else gets an
      // honest generic shape.
      responses: route.revealSemantics ? {
        200: {
          description: 'Disclosed.',
          content: { 'application/json': { schema: { $ref: '#/components/schemas/RandomReveal' } } },
        },
        425: {
          description: 'Still sealed: the commitment\'s reveal time has not been reached. Retry after `retry-after` seconds.',
          content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
        },
        404: {
          description: 'No such commitment (or it aged out of the retention window).',
          content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
        },
      } : {
        200: {
          description: 'OK. Free, unpaid response.',
          content: { 'application/json': { schema: { type: 'object', additionalProperties: true } } },
        },
        400: {
          description: 'The request was malformed. Nothing was charged, because nothing here is ever charged.',
          content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
        },
        404: {
          description: 'No such resource.',
          content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } },
        },
      },
      'x-payment-protocol': 'x402',
      'x-payment-info': { price: '0', currency: 'USDC', free: true, documentation: l.docFor(product.id) },
    },
  };
}

function discoveryPaths(l) {
  const free = (summary, description, schemaRef) => ({
    get: {
      operationId: `discovery_${summary.replace(/[^a-z0-9]+/gi, '_').toLowerCase()}`,
      summary,
      description,
      tags: ['discovery'],
      responses: {
        200: {
          description: 'OK',
          content: { 'application/json': { schema: schemaRef } },
        },
      },
    },
  });
  return {
    '/x402': {
      get: {
        operationId: 'discovery_catalog',
        summary: 'Product catalog (or this landing page)',
        description: 'Content-negotiated: `Accept: text/html` returns the human landing page, anything else returns the machine catalog. Free, unauthenticated.',
        tags: ['discovery'],
        parameters: [{
          name: 'accept', in: 'header', required: false, schema: { type: 'string' },
          description: 'text/html for the landing page; application/json (or */*) for the catalog',
        }],
        responses: {
          200: {
            description: 'Catalog JSON, or the landing page for browsers.',
            content: {
              'application/json': { schema: { $ref: '#/components/schemas/Catalog' } },
              'text/html': { schema: { type: 'string' } },
            },
          },
        },
      },
    },
    '/.well-known/x402': free('well known catalog', 'The same catalog, always JSON, at the ecosystem-conventional location. Free, unauthenticated.', { $ref: '#/components/schemas/Catalog' }),
    '/x402/openapi.json': free('openapi document', 'This document, generated from the live product registry.', { type: 'object', additionalProperties: true }),
    '/x402/stats': free('aggregate settlement stats', 'Aggregate counts from the settlement store: settled payments, last 24h, per-product counts, network and asset. No payer addresses, amounts or transaction hashes.', { $ref: '#/components/schemas/Stats' }),
    '/x402/healthz': free('gateway liveness', 'Process liveness for monitors.', { type: 'object', additionalProperties: true }),
  };
}

/**
 * Build the OpenAPI document.
 *
 * @param {object} opts.cfg      gateway config
 * @param {object} opts.catalog  the live catalog (prices + availability)
 * @param {Array}  opts.products registry product objects (routes + schemas)
 */
function buildOpenApi({ cfg, catalog, products = [] }) {
  const l = links(cfg);
  const id = identity(cfg);
  const facts = networkFacts(cfg);
  const byId = new Map(catalog.products.map((p) => [p.id, p]));

  const paths = discoveryPaths(l);
  const tagsUsed = new Set(['discovery']);

  for (const product of products) {
    if (!product.enabled && !product.listedEvenWhenUnavailable) continue;
    if (product.devOnly) continue; // the smoke-test echo is not a product
    const entry = byId.get(product.id);
    if (!entry) continue;
    const tag = TAGS[product.id] || 'products';
    tagsUsed.add(tag);
    for (const route of product.routes) {
      const op = paidOperation({ product, entry, route, facts, l, tag });
      paths[route.path] = paths[route.path] || {};
      paths[route.path][route.method.toLowerCase()] = op;
    }
    for (const route of product.freeRoutes || []) {
      const { path, method, op } = freeOperation({ product, route, l });
      paths[path] = paths[path] || {};
      paths[path][method] = op;
    }
  }

  const TAG_DESCRIPTIONS = {
    randomness: 'Randomness products. One signed node draw per request; derived results publish the rules to recompute them.',
    'chain-data': 'Bulk reads of the Animica L1. The free public APIs are unaffected — these sell range, batching and indexes they do not have.',
    inference: 'OpenAI-compatible inference, sold only while enough workers are live-serving.',
    discovery: 'Free, unauthenticated description of everything above.',
    products: 'Paid products.',
  };

  return {
    openapi: '3.1.1',
    info: {
      title: `${id.name} — paid agent APIs`,
      version: PKG_VERSION,
      summary: 'Pay-per-request APIs settled with x402. No account, no API key.',
      description: [
        'Machine-payable HTTP APIs from Animica. Every paid operation answers `402 Payment Required` with the exact terms (amount, asset, network, recipient, expiry); the caller signs an authorization for those terms locally and retries with it in the `payment-signature` header (x402 v2) or `x-payment` (v1). The gateway verifies the authorization, settles it on-chain through its own facilitator, and returns the response with the settlement in the `payment-response` header.',
        '',
        'There is NO API-key authentication anywhere in this document, and no per-customer account: the payment payload is the only credential. Private keys never reach Animica.',
        '',
        'Ordering guarantees, per product: cheap read products are produced BEFORE settlement (a failure charges nothing), while products with an expensive downstream re-check readiness immediately before settling and, if the downstream still fails afterwards, return a SIGNED error receipt referencing the settled payment. Products whose backend is unhealthy report `available: false` in the catalog and answer 503 without ever emitting a 402.',
        '',
        `Prices and availability in this document are generated from the live product registry (${l.wellKnown}); response examples are real captured responses.`,
      ].join('\n'),
      contact: { name: id.provider, url: id.homepage, email: id.contact },
      license: { name: id.license, identifier: 'Apache-2.0' },
      termsOfService: l.landing,
    },
    externalDocs: { description: 'Human documentation and live catalog', url: l.landing },
    servers: [{ url: l.base, description: 'Animica x402 gateway' }],
    'x-payment-protocol': 'x402',
    'x-x402': Object.assign({}, facts, {
      catalog: l.catalog,
      well_known: l.wellKnown,
      stats: l.stats,
      documentation: l.landing,
    }),
    tags: [...tagsUsed].map((t) => ({ name: t, description: TAG_DESCRIPTIONS[t] || undefined })),
    paths,
    components: baseComponents(),
  };
}

module.exports = { buildOpenApi, TAGS, anchorFor };
