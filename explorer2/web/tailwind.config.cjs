module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        night: {
          950: '#05060b',
          900: '#0b0f1f',
          800: '#123524',
          700: '#17422e',
          600: '#2a3447'
        },
        day: {
          50: '#f8f9fa',
          100: '#f1f3f5',
          200: '#e9ecef',
          300: '#dee2e6',
          400: '#ced4da'
        },
        animica: {
          400: '#8fe3ff',
          500: '#62c7ff',
          600: '#3ba7ff',
          700: '#1e90ff'
        }
      }
    }
  },
  plugins: []
}
