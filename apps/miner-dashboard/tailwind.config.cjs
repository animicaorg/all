/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
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
    },
  },
  plugins: [],
};
