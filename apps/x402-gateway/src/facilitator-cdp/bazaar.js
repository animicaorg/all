'use strict';
/**
 * The CDP Bazaar discovery extension — in CDP's dialect, which is not the one
 * our 402 publishes.
 *
 * TWO INDEXERS, TWO INCOMPATIBLE SHAPES FOR THE SAME KEY. Our 402 carries
 * `extensions.bazaar` in the shape x402scan and 402 Index parse: a FIELD
 * DESCRIPTOR map, `bodyFields: {limit: {type, required, description}}`. All 44
 * of our products are listed in 402 Index off exactly that, so it stays
 * untouched.
 *
 * CDP's facilitator reads the same key expecting something else entirely:
 *
 *   info.input.body / .queryParams   EXAMPLE VALUES, not descriptors
 *   info.output                      {type, example}, not {type, description}
 *   schema                           a JSON Schema that `info` must VALIDATE
 *                                    AGAINST — it is the thing being checked,
 *                                    and omitting it fails the check outright
 *
 * Send the 402's version and CDP answers, in the EXTENSION-RESPONSES header,
 * `{"bazaar":{"status":"rejected","rejectedReason":"invalid discovery
 * configuration"}}` — while settling the payment normally. That is why we
 * settled real payments through CDP for a day and appeared in 0 of 14,994
 * Bazaar resources: nothing in the payment path fails, so nothing surfaces.
 *
 * So the CDP shape is built HERE, for the facilitator call only, and the
 * published 402 keeps the dialect its own indexers already read. One key, two
 * consumers, two builders — rather than a shape that satisfies neither.
 *
 * NOTHING IS INVENTED. The schema is a faithful translation of the field
 * descriptors the product already declares. Example values come only from
 * VERIFIED sources — `discovery/samples.js` (responses captured against this
 * gateway) and `discovery/request-examples.js` (request bodies that actually
 * returned 200 from the live paid route). A product with neither publishes its
 * schema and no example, because a plausible-looking made-up example is
 * precisely the kind of thing an agent would then send us.
 */

const { SAMPLES } = require('../discovery/samples');
const { REQUEST_EXAMPLES, QUERY_EXAMPLES } = require('../discovery/request-examples');

const JSON_SCHEMA_DRAFT = 'https://json-schema.org/draft/2020-12/schema';
/** Descriptor types we publish, mapped to JSON Schema types. */
const TYPES = new Set(['string', 'number', 'integer', 'boolean', 'array', 'object']);

/** One declared field -> one JSON Schema property. Unknown types are omitted
 *  rather than guessed: a wrong `type` is worse than an absent one. */
function propertyOf(desc) {
  if (!desc || typeof desc !== 'object') return {};
  const out = {};
  if (TYPES.has(desc.type)) out.type = desc.type;
  if (typeof desc.description === 'string' && desc.description) out.description = desc.description;
  if (Array.isArray(desc.enum) && desc.enum.length) out.enum = desc.enum;
  if (desc.default !== undefined) out.default = desc.default;
  return out;
}

/** A descriptor map -> a JSON Schema object node, or null if there is nothing
 *  to say. `required` follows the descriptors, so it stays honest about what a
 *  caller must actually send. */
function objectSchemaOf(fields) {
  if (!fields || typeof fields !== 'object') return null;
  const names = Object.keys(fields);
  if (!names.length) return null;
  const properties = {};
  const required = [];
  for (const name of names) {
    properties[name] = propertyOf(fields[name]);
    if (fields[name] && fields[name].required) required.push(name);
  }
  const node = { type: 'object', properties };
  if (required.length) node.required = required;
  return node;
}

/** The captured sample for a product, if one exists. */
function sampleFor(productId) {
  return (productId && SAMPLES[productId]) || null;
}

/**
 * Build the CDP-dialect `bazaar` extension for one route.
 *
 * @param {object} route  the gate route (productId, outputSchema, ...)
 * @returns {object|null} the extension, or null when the product declares no
 *                        input/output shape at all and there is nothing to say
 */
function buildCdpBazaarExtension(route) {
  const os = route && route.outputSchema;
  if (!os || !os.input) return null;
  const method = String(os.input.method || 'POST').toUpperCase();

  const input = { type: 'http', method };

  const bodySchema = objectSchemaOf(os.input.bodyFields);
  const querySchema = objectSchemaOf(os.input.queryParams);

  // Example values, only where a VERIFIED one exists: a captured sample, or a
  // request body proven against the live endpoint (request-examples.js).
  const sample = sampleFor(route.productId);
  const bodyExample = (sample && sample.body && typeof sample.body === 'object')
    ? sample.body
    : REQUEST_EXAMPLES[route.productId];
  if (bodyExample && typeof bodyExample === 'object') input.body = bodyExample;

  // `bodyType` ONLY alongside a body. CDP's BodyDiscoveryInfo is "body input
  // AND type specification": declaring the type of a body that is not there was
  // rejected as an "invalid discovery configuration" on 30 of our products,
  // while the same products without the orphan key were accepted.
  if (os.input.bodyType && input.body) input.bodyType = os.input.bodyType;

  // A query example must be TYPED, not URL-shaped. Parsing `?bytes=32` yields
  // the string "32" while the schema beside it says integer, so the example
  // fails validation against its own schema — which is exactly how the one GET
  // product that had a sample got rejected while the ones with no example passed.
  const qExample = QUERY_EXAMPLES[route.productId] || null;
  if (qExample) input.queryParams = qExample;

  const info = { input };
  const outExample = sample && sample.response;
  if (outExample && typeof outExample === 'object') {
    info.output = { type: 'json', example: outExample };
  } else if (os.output && os.output.type) {
    info.output = { type: os.output.type };
  }

  // The schema describes `info` — the object CDP validates. Its input node
  // mirrors exactly the keys we just emitted, so validation cannot fail on a
  // property we described but did not send, or sent but did not describe.
  const inputProperties = {
    type: { type: 'string', const: 'http' },
    method: { type: 'string', enum: [method] },
  };
  if (input.bodyType) inputProperties.bodyType = { type: 'string', enum: [input.bodyType] };
  if (bodySchema) inputProperties.body = bodySchema;
  if (querySchema) inputProperties.queryParams = querySchema;

  const properties = {
    input: { type: 'object', properties: inputProperties, required: ['type', 'method'] },
  };
  if (info.output) {
    properties.output = {
      type: 'object',
      properties: { type: { type: 'string' }, example: {} },
      required: ['type'],
    };
  }

  return {
    info,
    schema: { $schema: JSON_SCHEMA_DRAFT, type: 'object', properties, required: ['input'] },
  };
}

module.exports = { buildCdpBazaarExtension, objectSchemaOf };
