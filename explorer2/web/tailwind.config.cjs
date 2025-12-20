module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        night: {
          950: '#05060b',
          900: '#0b0f1f',
          800: '#131a2b',
          700: '#1a2236'
        },
        animica: {
          400: '#8fe3ff',
          500: '#62c7ff',
          600: '#3ba7ff'
        }
      }
    }
  },
  plugins: []
}
