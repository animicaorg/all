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
  try {
    return require(pkg);
  } catch {
    return null;
  }
};

const hasTailwind =
  fs.existsSync(path.join(__dirname, 'tailwind.config.cjs')) &&
  !!tryLoad('tailwindcss');

const plugins = [];

// Resolve @import directives when available
const postcssImport = tryLoad('postcss-import');
if (postcssImport) plugins.push(postcssImport());

// Optional TailwindCSS support
if (hasTailwind) plugins.push(require('tailwindcss'));

// CSS nesting — prefer spec plugin, fall back to postcss-nested if present
const nesting = tryLoad('postcss-nesting') || tryLoad('postcss-nested');
if (nesting) plugins.push(nesting());

// Vendor prefixing
const autoprefixer = tryLoad('autoprefixer');
if (autoprefixer) plugins.push(autoprefixer());

module.exports = { plugins };
