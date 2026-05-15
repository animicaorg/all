/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{astro,html,js,jsx,ts,tsx,vue,svelte,md,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f1f5fb",
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
        teal: {
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#0d9488",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      typography: () => ({
        invert: {
          css: {
            "--tw-prose-body": "#dde6f4",
            "--tw-prose-headings": "#f1f5fb",
            "--tw-prose-links": "#fbbf24",
            "--tw-prose-bold": "#f1f5fb",
            "--tw-prose-code": "#fbbf24",
            "--tw-prose-pre-bg": "#070d1b",
            "--tw-prose-pre-code": "#dde6f4",
            "--tw-prose-quotes": "#b9c8e2",
            "--tw-prose-quote-borders": "#23375a",
          },
        },
      }),
    },
  },
  plugins: [],
};
