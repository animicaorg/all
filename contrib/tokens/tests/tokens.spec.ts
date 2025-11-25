// Animica Design Tokens — Tests
// Run with: npx vitest run (or pnpm vitest / yarn vitest)
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

let Ajv: any;
try {
  // Lazy import Ajv if available
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  Ajv = require('ajv');
} catch {
  Ajv = null;
}

const TOKENS_DIR = path.resolve(__dirname, '..');
const SCHEMAS_DIR = path.join(TOKENS_DIR, 'schemas');
const BUILD_DIR = path.join(TOKENS_DIR, 'build');

function readJson(p: string) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function safeExists(p: string) {
  try {
    fs.accessSync(p, fs.constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

describe('tokens: source files exist', () => {
  it('tokens.json exists', () => {
    const p = path.join(TOKENS_DIR, 'tokens.json');
    expect(safeExists(p)).toBe(true);
  });

  it('tokens.dark.json exists', () => {
    const p = path.join(TOKENS_DIR, 'tokens.dark.json');
    expect(safeExists(p)).toBe(true);
  });

  it('tokens.animations.json exists', () => {
    const p = path.join(TOKENS_DIR, 'tokens.animations.json');
    expect(safeExists(p)).toBe(true);
  });

  it('schema exists', () => {
    const p = path.join(SCHEMAS_DIR, 'tokens.schema.json');
    expect(safeExists(p)).toBe(true);
  });
});

describe('tokens: json schema validation (Ajv if installed)', () => {
  const schemaPath = path.join(SCHEMAS_DIR, 'tokens.schema.json');
  const basePath = path.join(TOKENS_DIR, 'tokens.json');
  const darkPath = path.join(TOKENS_DIR, 'tokens.dark.json');
  const animPath = path.join(TOKENS_DIR, 'tokens.animations.json');

  const schema = readJson(schemaPath);
  const base = readJson(basePath);
  const dark = readJson(darkPath);
  const anim = readJson(animPath);

  it('validates tokens.json', () => {
    if (!Ajv) {
      // Fallback structural checks
      expect(base).toBeDefined();
      const theme = (base as any).light ?? base;
      expect(theme.color).toBeDefined();
      expect(theme.typography).toBeDefined();
      expect(theme.space).toBeDefined();
      expect(theme.radius).toBeDefined();
      expect(theme.shadow).toBeDefined();
      return;
    }
    const ajv = new Ajv({ allErrors: true, strict: false });
    const validate = ajv.compile(schema);
    const doc = (base as any).version ? base : { version: '1.0.0', light: base };
    const ok = validate(doc);
    if (!ok) {
      // Print for debugging in CI
      // eslint-disable-next-line no-console
      console.error(validate.errors);
    }
    expect(ok).toBe(true);
  });

  it('validates tokens.dark.json as overrides merged with base', () => {
    if (!Ajv) {
      // Fallback checks
      const overrides = (dark as any).dark ?? dark;
      expect(typeof overrides).toBe('object');
      expect(Object.keys(overrides).length).toBeGreaterThan(0);
      return;
    }
    const ajv = new Ajv({ allErrors: true, strict: false });
    const validate = ajv.compile(schema);
    const doc = {
      version: (base as any).version ?? '1.0.0',
      light: (base as any).light ?? base,
      dark: (dark as any).dark ?? dark,
    };
    const ok = validate(doc);
    if (!ok) console.error(validate.errors);
    expect(ok).toBe(true);
  });

  it('validates tokens.animations.json attached as animation', () => {
    if (!Ajv) {
      const a = (anim as any).animation ?? anim;
      expect(a.duration).toBeDefined();
      expect(a.easing).toBeDefined();
      expect(a.presets).toBeDefined();
      return;
    }
    const ajv = new Ajv({ allErrors: true, strict: false });
    const validate = ajv.compile(schema);
    const doc = {
      version: (base as any).version ?? '1.0.0',
      light: (base as any).light ?? base,
      animation: (anim as any).animation ?? anim,
    };
    const ok = validate(doc);
    if (!ok) console.error(validate.errors);
    expect(ok).toBe(true);
  });
});

describe('tokens: color hex sanity (light + dark)', () => {
  const hexRe = /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$/;

  it('light theme hex colors are valid', () => {
    const base = readJson(path.join(TOKENS_DIR, 'tokens.json'));
    const theme = (base as any).light ?? base;
    const color = theme.color;
    for (const [group, scale] of Object.entries<string>(color)) {
      for (const [step, hex] of Object.entries<string>(scale as any)) {
        expect(hexRe.test(hex)).toBe(true);
      }
    }
  });

  it('dark overrides hex colors are valid', () => {
    const dark = readJson(path.join(TOKENS_DIR, 'tokens.dark.json'));
    const overrides = (dark as any).dark ?? dark;
    const color = overrides.color || {};
    for (const [group, scale] of Object.entries<any>(color)) {
      for (const [step, hex] of Object.entries<string>(scale)) {
        expect(hexRe.test(hex)).toBe(true);
      }
    }
  });
});

describe('build artifacts: presence & spot checks', () => {
  it('merged bundle exists and has required sections', () => {
    const p = path.join(BUILD_DIR, 'json', 'tokens.merged.json');
    expect(safeExists(p)).toBe(true);
    const merged = readJson(p);
    expect(merged.version).toBeDefined();
    expect(merged.light).toBeDefined();
    // dark/animation may be empty but should exist
    expect(merged.dark).toBeDefined();
    expect(merged.animation).toBeDefined();
  });

  it('css variables include primary-600 and a neutral-900 entries', () => {
    const css = fs.readFileSync(path.join(BUILD_DIR, 'css', 'tokens.css'), 'utf8');
    expect(css).toMatch(/--anm-color-primary-600:\s*#/);
    expect(css).toMatch(/--anm-color-neutral-900:\s*#/);
  });

  it('dark css overrides include data-theme selector', () => {
    const css = fs.readFileSync(path.join(BUILD_DIR, 'css', 'tokens.dark.css'), 'utf8');
    expect(css).toMatch(/\[data-theme="dark"\]\s*{/);
    expect(css).toMatch(/--anm-color-primary-600:\s*#/);
  });

  it('scss map file exists', () => {
    const p = path.join(BUILD_DIR, 'scss', '_tokens.scss');
    expect(safeExists(p)).toBe(true);
    const scss = fs.readFileSync(p, 'utf8');
    expect(scss).toMatch(/\$anm-color-primary:\s*\(/);
    expect(scss).toMatch(/@function anm-color\(/);
  });

  it('typescript export exists and exports tokens', () => {
    const p = path.join(BUILD_DIR, 'ts', 'tokens.ts');
    expect(safeExists(p)).toBe(true);
    const ts = fs.readFileSync(p, 'utf8');
    expect(ts).toMatch(/export const tokens = /);
    expect(ts).toMatch(/export default tokens;/);
  });

  it('dart bridge file exists (merged json const)', () => {
    const p = path.join(BUILD_DIR, 'dart', 'tokens.dart');
    expect(safeExists(p)).toBe(true);
    const dart = fs.readFileSync(p, 'utf8');
    expect(dart).toMatch(/const String kAnimicaTokensMergedJson/);
  });
});
