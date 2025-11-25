/**
 * Tailwind CSS config (optional) for Studio Web.
 * Safe defaults + CSS-variables-driven theming. Works with our PostCSS config,
 * which only loads Tailwind if it's installed.
 */
const plugin = (() => {
  try { return require('tailwindcss/plugin'); } catch { return () => () => {}; }
})();

module.exports = {
  content: [
    './index.html',
    './public/**/*.html',
    './src/**/*.{ts,tsx,css}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Bind to design tokens (see src/styles/tokens.css)
        primary: {
          DEFAULT: 'var(--color-primary)',
          foreground: 'var(--color-on-primary)',
          50: 'var(--color-primary-50)',
          100: 'var(--color-primary-100)',
          200: 'var(--color-primary-200)',
          300: 'var(--color-primary-300)',
          400: 'var(--color-primary-400)',
          500: 'var(--color-primary-500)',
          600: 'var(--color-primary-600)',
          700: 'var(--color-primary-700)',
          800: 'var(--color-primary-800)',
          900: 'var(--color-primary-900)',
        },
        surface: {
          DEFAULT: 'var(--color-surface)',
          soft: 'var(--color-surface-soft)',
          strong: 'var(--color-surface-strong)',
        },
        accent: 'var(--color-accent)',
        success: 'var(--color-success)',
        warning: 'var(--color-warning)',
        danger: 'var(--color-danger)',
        muted: 'var(--color-muted)',
        border: 'var(--color-border)',
        text: {
          DEFAULT: 'var(--color-text)',
          muted: 'var(--color-text-muted)',
          strong: 'var(--color-text-strong)',
          onSurface: 'var(--color-on-surface)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '10px',
        lg: '14px',
        xl: '18px',
      },
      boxShadow: {
        card: '0 4px 16px rgba(0,0,0,0.08)',
        focus: '0 0 0 3px rgba(99, 102, 241, 0.35)', // indigo-ish focus ring
      },
      spacing: {
        4.5: '1.125rem',
        18: '4.5rem',
      },
      container: {
        center: true,
        padding: '1rem',
        screens: {
          sm: '640px',
          md: '768px',
          lg: '1024px',
          xl: '1280px',
          '2xl': '1440px',
        },
      },
      animation: {
        'spin-slow': 'spin 2.5s linear infinite',
        'pulse-slow': 'pulse 2.5s ease-in-out infinite',
      },
    },
  },
  safelist: [
    // status & tag colors used dynamically
    'text-success', 'text-warning', 'text-danger',
    'bg-success/10', 'bg-warning/10', 'bg-danger/10',
    'border-success', 'border-warning', 'border-danger',
    // badge variants
    'bg-surface-strong', 'bg-surface-soft',
    'text-text-muted', 'text-text-strong',
  ],
  plugins: [
    // Load if available; otherwise the try/catch plugin no-ops.
    plugin(({ addVariant }) => {
      // data-state variants for headless UI components
      addVariant('state-open', '&[data-state="open"]');
      addVariant('state-closed', '&[data-state="closed"]');
      addVariant('state-active', '&[data-state="active"]');
    }),
  ],
};
