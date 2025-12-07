/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    screens: {
      xs: '360px',
      sm: '640px',
      md: '768px',
      lg: '1024px',
      xl: '1280px',
      '2xl': '1536px',
    },
    extend: {
      colors: {
        night: '#0b1021',
        indigo: {
          950: '#0e1234',
        },
        neon: '#7c3aed',
      },
      boxShadow: {
        card: '0 20px 70px rgba(0,0,0,0.25)',
      },
      spacing: {
        safe: 'env(safe-area-inset-bottom)',
      },
      minHeight: {
        touch: '44px',
      },
      minWidth: {
        touch: '44px',
      },
    },
  },
  plugins: [],
};
