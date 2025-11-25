/**
 * explorer-web PostCSS config
 * - Loads common plugins (import, nesting, autoprefixer)
 * - TailwindCSS is optional; if not installed, it's silently skipped.
 *   (See explorer-web/tailwind.config.cjs for an example config.)
 */

function optional(name) {
  try {
    return require(name);
  } catch {
    return null;
  }
}

const plugins = [];

// Optional: resolve @import rules
const postcssImport = optional('postcss-import');
if (postcssImport) plugins.push(postcssImport());

// Optional: Tailwind (utility classes)
const tailwindcss = optional('tailwindcss');
if (tailwindcss) plugins.push(tailwindcss());

// CSS Nesting (draft spec)
const nesting = optional('postcss-nesting') || optional('@csstools/postcss-nesting');
if (nesting) plugins.push(nesting());

// Vendor prefixes
const autoprefixer = optional('autoprefixer');
if (autoprefixer) plugins.push(autoprefixer());

module.exports = { plugins };
