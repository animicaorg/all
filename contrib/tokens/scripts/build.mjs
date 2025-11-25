// Animica Tokens Build Script (Style Dictionary–like)
// Transforms contrib/tokens/*.json → build targets (css, scss, ts, dart, json)
// Usage: node contrib/tokens/scripts/build.mjs [--out contrib/tokens/build]
// SPDX-License-Identifier: MIT

import fs from 'fs';
import path from 'path';
import url from 'url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TOKENS_DIR = path.join(ROOT, 'tokens');
const BUILD_DIR = getArg('--out') || path.join(TOKENS_DIR, 'build');

const PATHS = {
  src: {
    base: path.join(TOKENS_DIR, 'tokens.json'),
    dark: path.join(TOKENS_DIR, 'tokens.dark.json'),
    anim: path.join(TOKENS_DIR, 'tokens.animations.json'),
    schema: path.join(TOKENS_DIR, 'schemas', 'tokens.schema.json'), // optional at runtime
  },
  out: {
    root: BUILD_DIR,
    css: path.join(BUILD_DIR, 'css'),
    scss: path.join(BUILD_DIR, 'scss'),
    ts: path.join(BUILD_DIR, 'ts'),
    dart: path.join(BUILD_DIR, 'dart'),
    json: path.join(BUILD_DIR, 'json'),
  }
};

async function main() {
  banner('Animica Tokens Build');

  // 1) Read sources (base is required; dark/anim optional)
  const base = readJsonRequired(PATHS.src.base, 'tokens.json');
  const dark = readJsonOptional(PATHS.src.dark) ?? {};
  const anim = readJsonOptional(PATHS.src.anim) ?? {};

  // 2) Compose merged website bundle
  const merged = {
    version: base.version ?? '1.0.0',
    light: base.light ?? base, // allow raw theme in tokens.json
    dark: dark.dark ?? dark,   // allow raw overrides in tokens.dark.json
    animation: anim.animation ?? anim
  };

  // 3) Ensure dirs
  ensureDir(PATHS.out.root);
  ensureDir(PATHS.out.css);
  ensureDir(PATHS.out.scss);
  ensureDir(PATHS.out.ts);
  ensureDir(PATHS.out.dart);
  ensureDir(PATHS.out.json);

  // 4) Emit JSON bundle
  const mergedJsonPath = path.join(PATHS.out.json, 'tokens.merged.json');
  writePrettyJson(mergedJsonPath, merged);

  // 5) Emit CSS variables (light)
  const cssLightPath = path.join(PATHS.out.css, 'tokens.css');
  fs.writeFileSync(cssLightPath, renderCssVariables(merged.light, { themeAttr: null, prefix: 'anm' }), 'utf8');

  // 6) Emit CSS variables (dark overrides under [data-theme="dark"])
  const cssDarkPath = path.join(PATHS.out.css, 'tokens.dark.css');
  fs.writeFileSync(cssDarkPath, renderCssDark(merged.dark, { prefix: 'anm' }), 'utf8');

  // 7) Emit SCSS map(s)
  const scssPath = path.join(PATHS.out.scss, '_tokens.scss');
  fs.writeFileSync(scssPath, renderScss(merged), 'utf8');

  // 8) Emit TypeScript bundle
  const tsPath = path.join(PATHS.out.ts, 'tokens.ts');
  fs.writeFileSync(tsPath, renderTs(merged), 'utf8');

  // 9) Emit Dart ThemeData bridge
  const dartPath = path.join(PATHS.out.dart, 'tokens.dart');
  fs.writeFileSync(dartPath, renderDart(merged), 'utf8');

  // 10) Done
  logOk('Wrote outputs:');
  console.log('  -', rel(mergedJsonPath));
  console.log('  -', rel(cssLightPath));
  console.log('  -', rel(cssDarkPath));
  console.log('  -', rel(scssPath));
  console.log('  -', rel(tsPath));
  console.log('  -', rel(dartPath));
  console.log('');
  console.log('Tip: import CSS in web apps, SCSS in design systems, TS for runtime, Dart for Flutter.');
}

/* ----------------- Renderers ----------------- */

function renderCssVariables(theme, { themeAttr = null, prefix = 'anm' } = {}) {
  // theme: { color, typography, space, radius, shadow }
  const sel = themeAttr ? `${themeAttr}` : `:root`;
  const lines = [];
  lines.push(`/* Animica Tokens — Light CSS Variables */`);
  lines.push(`/* SPDX-License-Identifier: MIT */`);
  lines.push(`${sel} {`);

  // Colors
  if (theme.color) {
    for (const [group, scale] of Object.entries(theme.color)) {
      for (const [step, hex] of Object.entries(scale)) {
        lines.push(`  --${prefix}-color-${group}-${step}: ${hex};`);
      }
    }
  }

  // Typography
  const T = theme.typography || {};
  if (T.fontFamily) {
    lines.push(`  --${prefix}-typography-font-base: ${T.fontFamily.base};`);
    lines.push(`  --${prefix}-typography-font-code: ${T.fontFamily.code};`);
  }
  if (T.scale) for (const [k, v] of Object.entries(T.scale)) lines.push(`  --${prefix}-typography-size-${k}: ${v}px;`);
  if (T.lineHeight) for (const [k, v] of Object.entries(T.lineHeight)) lines.push(`  --${prefix}-typography-line-${k}: ${v};`);
  if (T.tracking) for (const [k, v] of Object.entries(T.tracking)) lines.push(`  --${prefix}-typography-track-${k}: ${v}em;`);
  if (T.weight) for (const [k, v] of Object.entries(T.weight)) lines.push(`  --${prefix}-typography-weight-${k}: ${v};`);

  // Space
  if (theme.space) for (const [k, v] of Object.entries(theme.space)) lines.push(`  --${prefix}-space-${k}: ${v}px;`);

  // Radius
  if (theme.radius) {
    for (const [k, v] of Object.entries(theme.radius)) {
      const key = k === '2xl' ? '2xl' : k;
      lines.push(`  --${prefix}-radius-${key}: ${v}px;`);
    }
  }

  // Shadows
  if (theme.shadow) for (const [k, v] of Object.entries(theme.shadow)) lines.push(`  --${prefix}-shadow-${k}: ${v};`);

  lines.push(`}`);
  lines.push('');
  return lines.join('\n');
}

function renderCssDark(darkOverrides, { prefix = 'anm' } = {}) {
  const lines = [];
  lines.push(`/* Animica Tokens — Dark CSS Overrides */`);
  lines.push(`[data-theme="dark"] {`);
  if (darkOverrides?.color) {
    for (const [group, scale] of Object.entries(darkOverrides.color)) {
      for (const [step, hex] of Object.entries(scale)) {
        lines.push(`  --${prefix}-color-${group}-${step}: ${hex};`);
      }
    }
  }
  if (darkOverrides?.shadow) {
    for (const [k, v] of Object.entries(darkOverrides.shadow)) {
      lines.push(`  --${prefix}-shadow-${k}: ${v};`);
    }
  }
  lines.push(`}`);
  lines.push('');
  return lines.join('\n');
}

function renderScss(merged) {
  // Keeps parity with the hand-authored SCSS maps; enough for most DS use.
  const L = merged.light;
  const hdr = `/* Animica Tokens (SCSS) — generated */
/* Source: contrib/tokens/tokens.json (+ dark overrides)
 * SPDX-License-Identifier: MIT
 */`;

  const mapToScss = (name, obj) => {
    const entries = Object.entries(obj)
      .map(([k, v]) => `  ${k}: ${v}${typeof v === 'number' ? 'px' : ''}`)
      .join(',\n');
    return `$${name}: (\n${entries}\n) !default;`;
  };

  const colorMap = (name, scale) => {
    const entries = Object.entries(scale)
      .map(([k, v]) => `  ${k}:  ${v}`)
      .join(',\n');
    return `$anm-color-${name}: (\n${entries}\n) !default;`;
  };

  return [
    hdr,
    '',
    colorMap('primary', L.color.primary),
    '',
    colorMap('neutral', L.color.neutral),
    '',
    colorMap('surface', L.color.surface),
    '',
    colorMap('success', L.color.success),
    '',
    colorMap('warning', L.color.warning),
    '',
    colorMap('error', L.color.error),
    '',
    mapToScss('anm-typography-size', L.typography.scale),
    mapToScss('anm-typography-line', L.typography.lineHeight),
    mapToScss('anm-typography-weight', L.typography.weight),
    mapToScss('anm-space', L.space),
    mapToScss('anm-radius', L.radius),
    mapToScss('anm-shadow', L.shadow),
    '',
    `@function anm-color($group, $step) {
  $map: null;
  @if $group == primary   { $map = $anm-color-primary; }
  @else if $group == neutral { $map = $anm-color-neutral; }
  @else if $group == surface { $map = $anm-color-surface; }
  @else if $group == success { $map = $anm-color-success; }
  @else if $group == warning { $map = $anm-color-warning; }
  @else if $group == error   { $map = $anm-color-error; }
  @else { @error "Unknown color group `#{$group}`."; }
  @return map-get($map, $step);
}`,
    ''
  ].join('\n');
}

function renderTs(merged) {
  // Minimal TS export replicating tokens for web apps.
  return `// Generated by build.mjs — do not edit manually
// SPDX-License-Identifier: MIT
export const tokens = ${JSON.stringify(merged, null, 2)} as const;
export type Tokens = typeof tokens;
export default tokens;
`;
}

function renderDart(merged) {
  // Provide only the merged JSON; the full Flutter bridge lives in tokens.dart hand-authored.
  return `// Generated bundle (JSON as Dart const) — for advanced usages
// SPDX-License-Identifier: MIT
// Prefer importing the hand-authored ThemeData bridge in contrib/tokens/build/dart/tokens.dart

const String kAnimicaTokensMergedJson = r'''${JSON.stringify(merged)}''';
`;
}

/* ----------------- Helpers ----------------- */

function readJsonRequired(p, label) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    fail(\`Missing or invalid \${label}: \${rel(p)}\`);
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

function writePrettyJson(p, obj) {
  fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n', 'utf8');
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function getArg(flag) {
  const i = process.argv.indexOf(flag);
  if (i >= 0 && i + 1 < process.argv.length) return path.resolve(process.cwd(), process.argv[i + 1]);
  return null;
}

function rel(p) { return path.relative(path.join(__dirname, '..', '..', '..'), p) || p; }

function banner(msg) {
  console.log('='.repeat(64));
  console.log(msg);
  console.log('='.repeat(64));
}

function logOk(msg) { console.log('✔', msg); }
function fail(msg) { console.error('✖', msg); process.exit(1); }

await main();
