/** @type {import('tailwindcss').Config} */
/**
 * Optional TailwindCSS configuration for explorer-web.
 * Safe to keep in the repo even if Tailwind isn't installed (the build will just ignore it).
 *
 * - Uses CSS variables from src/styles/theme.css so design tokens can be themed (light/dark).
 * - Dark mode via `.dark` class on <html> or <body>.
 * - Conservative defaults; add plugins as needed.
 */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx,js,jsx,html,css}"
  ],
  darkMode: "class",
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: { "2xl": "1280px" }
    },
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "Noto Sans", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "sans-serif"]
      },
      colors: {
        // These map to CSS variables defined in src/styles/theme.css
        bg: "rgb(var(--color-bg) / <alpha-value>)",
        surface: "rgb(var(--color-surface) / <alpha-value>)",
        fg: "rgb(var(--color-fg) / <alpha-value>)",
        muted: "rgb(var(--color-muted) / <alpha-value>)",
        border: "rgb(var(--color-border) / <alpha-value>)",
        brand: {
          DEFAULT: "rgb(var(--color-brand) / <alpha-value>)",
          fg: "rgb(var(--color-brand-fg) / <alpha-value>)",
          subtle: "rgb(var(--color-brand-subtle) / <alpha-value>)"
        },
        success: "rgb(var(--color-success) / <alpha-value>)",
        warning: "rgb(var(--color-warning) / <alpha-value>)",
        danger: "rgb(var(--color-danger) / <alpha-value>)",
        info: "rgb(var(--color-info) / <alpha-value>)"
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.05), 0 1px 3px 1px rgb(0 0 0 / 0.05)"
      },
      borderRadius: {
        sm: "6px",
        DEFAULT: "10px",
        lg: "14px"
      }
    }
  },
  // Keep plugins empty by default to avoid hard deps.
  // Add e.g. require('@tailwindcss/typography') if you install it.
  plugins: []
};
