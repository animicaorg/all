/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{astro,html,js,jsx,ts,tsx,vue,svelte}"],
  theme: {
    extend: {
      colors: {
        // Brand-aligned palette mirroring animica-website.
        ink: {
          50:  "#f1f5fb",
          100: "#dde6f4",
          200: "#b9c8e2",
          300: "#8ea3c6",
          400: "#5e7aa5",
          500: "#3d5a87",
          600: "#2e4870",
          700: "#23375a",
          800: "#1a2945",
          900: "#0e1830",
          950: "#070d1b",
        },
        amber: {
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
