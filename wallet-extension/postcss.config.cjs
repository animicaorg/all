/**
 * PostCSS config for Animica Wallet (MV3).
 *
 * This is optional — it enables a small set of CSS conveniences for the UI
 * (popup/onboarding/approve). If a plugin isn't installed, it's skipped so
 * `vite` still runs without failing hard.
 *
 * Plugins:
 *  - postcss-import      : @import support
 *  - postcss-nesting     : CSS Nesting Module (or postcss-nested fallback)
 *  - autoprefixer        : vendor prefixing for target browsers
 */

function tryRequire(name) {
  try {
    return require(name);
  } catch {
    return null;
  }
}

const plugins = [];

// @import support
const pImport = tryRequire('postcss-import');
if (pImport) plugins.push(pImport());

// CSS nesting (prefer spec plugin; fallback to postcss-nested if present)
const pNesting = tryRequire('postcss-nesting') || tryRequire('postcss-nested');
if (pNesting) plugins.push(pNesting());

// Autoprefixer (uses browserslist from package.json if present)
const pAutoprefixer = tryRequire('autoprefixer');
if (pAutoprefixer) plugins.push(pAutoprefixer());

module.exports = {
  plugins,
};
