/**
 * PostCSS config for Studio Web.
 * - postcss-import: resolves @import directives
 * - tailwindcss: (optional) loaded if installed & tailwind.config.cjs exists
 * - postcss-nesting: enables CSS Nesting per spec
 * - autoprefixer: adds vendor prefixes based on browserslist
 */
const fs = require('fs');
const path = require('path');

const tryLoad = (pkg) => {
  try { return require(pkg); } catch { return null; }
};

const hasTailwind =
  fs.existsSync(path.join(__dirname, 'tailwind.config.cjs')) &&
  !!tryLoad('tailwindcss');

const plugins = [
  require('postcss-import'),
  hasTailwind && require('tailwindcss'),
  require('postcss-nesting'),
  require('autoprefixer'),
].filter(Boolean);

module.exports = { plugins };
