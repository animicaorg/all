'use strict';
/**
 * THE SMALL-MODEL UTILITY ENGINE.
 *
 * Every endpoint built on this file sells the same thing: unstructured text in,
 * STRICTLY VALIDATED JSON out. That validation is the entire product. An agent
 * can already talk to a model; what it cannot do cheaply is trust the shape of
 * what comes back. So the contract here is: either the response conforms to the
 * requested schema, or the call fails and says why — never "here is some JSON,
 * good luck".
 *
 * HOW THAT IS ENFORCED:
 *   1. The schema is sent to the model as an instruction.
 *   2. The reply is parsed defensively (models fence JSON in prose and markdown).
 *   3. It is validated against the schema HERE, in code — not trusted.
 *   4. On failure the model is re-asked ONCE with the specific validation error,
 *      which is the single highest-yield repair a small model responds to.
 *   5. If it still fails, the caller gets an error naming the violated
 *      constraint. Returning unvalidated output would defeat the point of
 *      buying this instead of calling a model directly.
 *
 * WHY A HAND-WRITTEN VALIDATOR. This gateway keeps its dependency surface tiny
 * (three audited packages) because it handles money. A JSON Schema subset —
 * type, required, properties, items, enum, min/max, additionalProperties — is
 * a few dozen lines and covers what agents actually send. Anything outside the
 * subset is REPORTED as unsupported rather than silently ignored, because a
 * validator that quietly skips a constraint is worse than no validator.
 */

const { ProductError, ProductUnavailable } = require('./errors');

const SUPPORTED_KEYWORDS = new Set([
  'type', 'properties', 'required', 'items', 'enum', 'const', 'description',
  'additionalProperties', 'minimum', 'maximum', 'minLength', 'maxLength',
  'minItems', 'maxItems', 'nullable', 'title', 'examples', 'default', '$schema',
]);

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** Keywords we do not implement, so a caller is never silently under-validated. */
function unsupportedKeywords(schema, path = '', out = []) {
  if (!schema || typeof schema !== 'object') return out;
  for (const k of Object.keys(schema)) {
    if (!SUPPORTED_KEYWORDS.has(k)) out.push(`${path}${k}`);
  }
  if (schema.properties && typeof schema.properties === 'object') {
    for (const [k, v] of Object.entries(schema.properties)) {
      unsupportedKeywords(v, `${path}properties.${k}.`, out);
    }
  }
  if (schema.items) unsupportedKeywords(schema.items, `${path}items.`, out);
  return out;
}

function typeOf(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (Number.isInteger(v)) return 'integer';
  return typeof v;   // string | number | boolean | object
}

/** Validate `value` against a JSON Schema subset. Returns [] or a list of errors. */
function validate(value, schema, path = '$') {
  const errs = [];
  if (!schema || typeof schema !== 'object') return errs;

  if (schema.type) {
    const want = Array.isArray(schema.type) ? schema.type : [schema.type];
    const got = typeOf(value);
    // An integer satisfies "number"; nothing else is coerced, because silently
    // accepting "5" for a number is exactly the bug this endpoint prevents.
    const ok = want.some((t) => t === got || (t === 'number' && got === 'integer')
      || (t === 'null' && value === null));
    if (!ok) errs.push(`${path}: expected ${want.join('|')}, got ${got}`);
  }
  if (schema.enum && Array.isArray(schema.enum)) {
    const hit = schema.enum.some((e) => JSON.stringify(e) === JSON.stringify(value));
    if (!hit) errs.push(`${path}: must be one of ${JSON.stringify(schema.enum)}`);
  }
  if (schema.const !== undefined && JSON.stringify(value) !== JSON.stringify(schema.const)) {
    errs.push(`${path}: must equal ${JSON.stringify(schema.const)}`);
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errs.push(`${path}: shorter than minLength ${schema.minLength}`);
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      errs.push(`${path}: longer than maxLength ${schema.maxLength}`);
    }
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) errs.push(`${path}: below minimum ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errs.push(`${path}: above maximum ${schema.maximum}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errs.push(`${path}: fewer than minItems ${schema.minItems}`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errs.push(`${path}: more than maxItems ${schema.maxItems}`);
    if (schema.items) value.forEach((v, i) => errs.push(...validate(v, schema.items, `${path}[${i}]`)));
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const r of (schema.required || [])) {
      if (!(r in value)) errs.push(`${path}.${r}: required property missing`);
    }
    if (schema.properties) {
      for (const [k, sub] of Object.entries(schema.properties)) {
        if (k in value) errs.push(...validate(value[k], sub, `${path}.${k}`));
      }
      if (schema.additionalProperties === false) {
        for (const k of Object.keys(value)) {
          if (!(k in schema.properties)) errs.push(`${path}.${k}: additional property not allowed`);
        }
      }
    }
  }
  return errs;
}

/**
 * Pull JSON out of a model reply. Small models fence it, prefix it with prose,
 * or emit it bare; all three are normal and none is an error worth charging a
 * retry for, so they are handled here rather than in the prompt.
 */
function extractJson(text) {
  if (typeof text !== 'string') return null;
  const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(text);
  const candidates = [];
  if (fenced) candidates.push(fenced[1]);
  candidates.push(text);
  // Also try the outermost {...} or [...] span.
  const first = Math.min(
    ...[text.indexOf('{'), text.indexOf('[')].filter((i) => i >= 0).concat([Infinity]));
  if (Number.isFinite(first)) {
    const lastBrace = Math.max(text.lastIndexOf('}'), text.lastIndexOf(']'));
    if (lastBrace > first) candidates.push(text.slice(first, lastBrace + 1));
  }
  for (const c of candidates) {
    try {
      const v = JSON.parse(String(c).trim());
      if (v !== null && typeof v === 'object') return v;
    } catch { /* try the next candidate */ }
  }
  return null;
}

/**
 * The shared engine: ask a small model for JSON matching `schema`, validate it,
 * repair once, and refuse rather than return something unvalidated.
 */
function createEngine({ cfg, fetchImpl = fetch, now = Date.now }) {
  async function raw(messages, maxTokens) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.utilityInferenceKey) headers.authorization = `Bearer ${cfg.utilityInferenceKey}`;
    let r;
    try {
      r = await fetchImpl(cfg.utilityInferenceUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model: cfg.utilityModel,
          messages,
          max_tokens: maxTokens || Number(cfg.utilityMaxTokens),
          temperature: 0,          // structure, not creativity
        }),
        signal: AbortSignal.timeout(Number(cfg.utilityTimeoutMs)),
      });
    } catch (e) {
      const err = new Error(`utility model unreachable: ${e.message}`);
      err.retryable = true;
      throw err;
    }
    if (!r.ok) {
      const err = new Error(`utility model HTTP ${r.status}`);
      err.retryable = r.status >= 500;
      throw err;
    }
    const j = await r.json();
    const t = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof t !== 'string' || !t.trim()) {
      const err = new Error('utility model returned no content');
      err.retryable = true;
      throw err;
    }
    return { text: t, model: (j && j.model) || cfg.utilityModel };
  }

  /**
   * Returns { data, model, attempts, repaired }. Throws a ProductError naming
   * the violated constraint when the model cannot produce conforming output —
   * an honest failure beats unvalidated JSON.
   */
  async function structured({ instruction, input, schema, maxTokens }) {
    const schemaText = JSON.stringify(schema);
    const sys =
      'You convert input into JSON. Reply with ONE JSON value and NOTHING else: '
      + 'no prose, no explanation, no markdown fence. It MUST validate against this JSON Schema:\n'
      + schemaText + '\n'
      + 'Use only information present in the input. When a field cannot be determined from the '
      + 'input, use null (or omit it if it is not required) rather than guessing a plausible value.';
    const messages = [
      { role: 'system', content: sys },
      { role: 'user', content: `${instruction}\n\nINPUT:\n${input}` },
    ];

    let attempts = 0;
    let lastErrs = null;
    let lastText = '';
    let model = null;

    for (let i = 0; i < 2; i++) {
      attempts += 1;
      const out = await raw(messages, maxTokens);
      model = out.model;
      lastText = out.text;
      const parsed = extractJson(out.text);
      if (parsed) {
        const errs = validate(parsed, schema);
        if (!errs.length) return { data: parsed, model, attempts, repaired: i > 0 };
        lastErrs = errs;
      } else {
        lastErrs = ['reply was not parseable as JSON'];
      }
      // One repair pass, quoting the exact violation. This is the single
      // highest-yield correction for a small model — vague "try again" is not.
      messages.push({ role: 'assistant', content: out.text.slice(0, 2000) });
      messages.push({
        role: 'user',
        content: 'That was rejected by the schema validator:\n'
          + lastErrs.slice(0, 8).map((e) => `- ${e}`).join('\n')
          + '\nReturn corrected JSON only. No prose, no fence.',
      });
    }

    throw new ProductError(
      'the model could not produce output matching your schema',
      {
        body: {
          error: 'schema_validation_failed',
          detail: 'the model could not produce output matching your schema after a repair attempt',
          validation_errors: (lastErrs || []).slice(0, 12),
          attempts,
          model,
          raw_preview: String(lastText).slice(0, 400),
          note: 'nothing conforming was produced, so nothing conforming is returned. Simplify the schema, or supply input that actually contains the fields you asked for.',
        },
      }
    );
  }

  async function health() {
    try {
      const r = await fetchImpl(cfg.utilityHealthUrl, { signal: AbortSignal.timeout(5000) });
      return r.ok ? { available: true } : { available: false, reason: 'utility_model_unavailable', detail: `health HTTP ${r.status}` };
    } catch (e) {
      return { available: false, reason: 'utility_model_unavailable', detail: e.message };
    }
  }

  return { structured, raw, health };
}

module.exports = {
  createEngine, validate, extractJson, unsupportedKeywords, typeOf, bad,
};
