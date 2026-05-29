/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f5f7fb',
          100: '#e6ebf5',
          200: '#c9d2e3',
          300: '#9aa6c0',
          400: '#6b7796',
          500: '#475068',
          600: '#2f3650',
          700: '#1f2438',
          800: '#13172a',
          900: '#0a0c1a',
          950: '#04050d',
        },
        accent: {
          50: '#eef4ff',
          400: '#7aa2ff',
          500: '#4f7aff',
          600: '#345dff',
          700: '#2b48d3',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(79,122,255,0.25), 0 8px 24px rgba(79,122,255,0.18)',
      },
    },
  },
  plugins: [],
};
