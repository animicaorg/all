// Animica Design Tokens — Validator
// Validates contrib/tokens/{tokens.json,tokens.dark.json,tokens.animations.json}
// against contrib/tokens/schemas/tokens.schema.json (when available).
// Tries to use Ajv if installed; otherwise runs lightweight structural checks.
// Usage: node contrib/tokens/scripts/validate.mjs
// Exit codes: 0 = ok, 1 = failures
// SPDX-License-Identifier: MIT

import fs from 'fs';
import path from 'path';
import url from 'url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TOKENS_DIR = path.join(ROOT);
const SCHEMAS_DIR = path.join(TOKENS_DIR, 'schemas');

const PATHS = {
  base: path.join(TOKENS_DIR, 'tokens.json'),
  dark: path.join(TOKENS_DIR, 'tokens.dark.json'),
  anim: path.join(TOKENS_DIR, 'tokens.animations.json'),
  schema: path.join(SCHEMAS_DIR, 'tokens.schema.json'),
};

let useAjv = false;
let Ajv;

/* -------------------- Main -------------------- */

(async function main() {
  console.log('== Animica Tokens Validation ==');

  const base = readJsonRequired(PATHS.base, 'tokens.json');
  const dark = readJsonOptional(PATHS.dark);
  const anim = readJsonOptional(PATHS.anim);

  // Try to load Ajv (optional dependency)
  try {
    ({ default: Ajv } = await import('ajv'));
    useAjv = true;
  } catch {
    useAjv = false;
  }

  const errors = [];

  // Schema validation (Ajv preferred)
  if (useAjv && fs.existsSync(PATHS.schema)) {
    const schema = readJsonRequired(PATHS.schema, 'tokens.schema.json');
    const ajv = new Ajv({ allErrors: true, strict: false });

    // Validate base tokens.json as full bundle (it may already be a full "light" theme with version)
    let baseDoc = base.version ? base : { version: base.version ?? '1.0.0', light: base };
    validateDoc(ajv, schema, baseDoc, 'tokens.json', errors);

    // Validate dark overrides by composing a minimal bundle using base as light
    if (dark) {
      const darkDoc = {
        version: base.version ?? '1.0.0',
        light: base.light ?? base,
        dark: dark.dark ?? dark
      };
      validateDoc(ajv, schema, darkDoc, 'tokens.dark.json', errors);
    }

    // Validate animations by composing as top-level "animation"
    if (anim) {
      const animDoc = {
        version: base.version ?? '1.0.0',
        light: base.light ?? base,
        animation: anim.animation ?? anim
      };
      validateDoc(ajv, schema, animDoc, 'tokens.animations.json', errors);
    }
  } else {
    // Lightweight structural checks if Ajv or schema missing
    if (!useAjv) {
      warn('Ajv not found; running basic structural checks. Install with: npm i -D ajv');
    }
    if (!fs.existsSync(PATHS.schema)) {
      warn(`Schema missing at ${rel(PATHS.schema)}; running basic structural checks.`);
    }
    structuralChecks(base, 'tokens.json', errors);
    if (dark) structuralChecksDark(dark, 'tokens.dark.json', errors);
    if (anim) structuralChecksAnim(anim, 'tokens.animations.json', errors);
  }

  // Linting: hex color format & key sanity
  lintColors(base, 'tokens.json', errors);
  if (dark) lintColorsDark(dark, 'tokens.dark.json', errors);

  // Report
  if (errors.length) {
    console.error('\n✖ Validation failed with the following issues:');
    for (const e of errors) console.error('  -', e);
    process.exit(1);
  } else {
    console.log('\n✔ All token files are valid.');
    process.exit(0);
  }
})();

/* -------------------- Helpers -------------------- */

function validateDoc(ajv, schema, doc, label, errors) {
  const validate = ajv.compile(schema);
  const ok = validate(doc);
  if (!ok) {
    const prefix = `${label}:`;
    for (const err of validate.errors ?? []) {
      errors.push(`${prefix} ${err.instancePath || '/'} ${err.message}${formatParams(err.params)}`);
    }
  } else {
    console.log(`✔ ${label} passed JSON Schema validation.`);
  }
}

function formatParams(p) {
  const keys = Object.keys(p || {});
  if (!keys.length) return '';
  const compact = keys.map(k => `${k}=${JSON.stringify(p[k])}`).join(', ');
  return compact ? ` (${compact})` : '';
}

function structuralChecks(doc, label, errors) {
  // Expect either full bundle {version, light:{...}} or just a "theme" object.
  const isBundle = typeof doc === 'object' && ('light' in doc || ('color' in doc && 'typography' in doc));
  if (!isBundle) {
    errors.push(`${label}: expected a token bundle with 'light' theme or a raw theme object containing {color, typography}.`);
    return;
  }
  const theme = doc.light ?? doc;
  for (const key of ['color', 'typography', 'space', 'radius', 'shadow']) {
    if (!(key in theme)) errors.push(`${label}: missing required key '${key}' in theme.`);
  }
  if (!theme.typography?.scale?.base) {
    errors.push(`${label}: typography.scale.base missing.`);
  }
  if (!theme.color?.primary) {
    errors.push(`${label}: color.primary missing.`);
  }
}

function structuralChecksDark(doc, label, errors) {
  const overrides = doc.dark ?? doc;
  const allowed = new Set(['color', 'shadow']);
  const keys = Object.keys(overrides || {});
  if (!keys.length) {
    errors.push(`${label}: empty overrides; provide at least 'color' or 'shadow'.`);
  }
  for (const k of keys) {
    if (!allowed.has(k)) {
      errors.push(`${label}: unknown override key '${k}'. Allowed: color, shadow.`);
    }
  }
}

function structuralChecksAnim(doc, label, errors) {
  const a = doc.animation ?? doc;
  if (!a.duration || !a.easing || !a.presets) {
    errors.push(`${label}: expected {duration, easing, presets} keys.`);
  }
}

function lintColors(doc, label, errors) {
  const theme = doc.light ?? doc;
  if (!theme?.color) return;
  const hexRe = /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$/;
  for (const [group, scale] of Object.entries(theme.color)) {
    for (const [step, hex] of Object.entries(scale)) {
      if (typeof hex !== 'string' || !hexRe.test(hex)) {
        errors.push(`${label}: color ${group}.${step} has invalid hex '${hex}'.`);
      }
    }
  }
}

function lintColorsDark(doc, label, errors) {
  const overrides = doc.dark ?? doc;
  if (!overrides?.color) return;
  const hexRe = /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$/;
  for (const [group, scale] of Object.entries(overrides.color)) {
    for (const [step, hex] of Object.entries(scale)) {
      if (typeof hex !== 'string' || !hexRe.test(hex)) {
        errors.push(`${label}: color override ${group}.${step} has invalid hex '${hex}'.`);
      }
    }
  }
}

function readJsonRequired(p, label) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    fail(`Could not read ${label} at ${rel(p)}: ${e.message}`);
  }
}

function readJsonOptional(p) {
  try {
    if (!fs.existsSync(p)) return null;
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function warn(msg) { console.warn('! ' + msg); }
function fail(msg) { console.error('✖ ' + msg); process.exit(1); }
function rel(p) { return path.relative(path.join(__dirname, '..', '..', '..'), p) || p; }
