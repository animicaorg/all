import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Inter", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "monospace"],
      },
      colors: {
        ink: {
          950: "#06070c",
          900: "#0c0e15",
          800: "#141821",
          700: "#1c2230",
          600: "#2a3142",
          500: "#3b4356",
          400: "#5a657d",
          300: "#8a93a8",
          200: "#b8becd",
          100: "#dde1ea",
          50: "#f3f5fa",
        },
        brand: {
          50: "#f4f7ff",
          100: "#e6edff",
          300: "#9cb3ff",
          500: "#5b7cfb",
          600: "#3d5dee",
          700: "#2c46c7",
          900: "#15214f",
        },
        accent: {
          300: "#7ee0b4",
          400: "#5bd6a3",
          500: "#3acb8a",
          600: "#1faf6e",
          700: "#16965e",
        },
      },
      boxShadow: {
        glass: "inset 0 1px 0 0 rgba(255,255,255,0.05), 0 24px 64px -16px rgba(15,20,40,0.55)",
        glow: "0 0 64px -8px rgba(91,124,251,0.45)",
      },
      backgroundImage: {
        "grid-faint":
          "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)",
      },
      keyframes: {
        floatGlow: {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)", opacity: "0.55" },
          "50%": { transform: "translate3d(0,-20px,0) scale(1.05)", opacity: "0.85" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        floatGlow: "floatGlow 12s ease-in-out infinite",
        shimmer: "shimmer 2.4s linear infinite",
        fadeUp: "fadeUp 0.5s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
