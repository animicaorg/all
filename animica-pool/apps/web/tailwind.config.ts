import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Brand accents (kept — existing pages reference these).
        neon: { green: "#39ff14", violet: "#8b5cf6", blue: "#38bdf8", teal: "#2dd4bf", cyan: "#22d3ee" },
        // Near-black ink scale (kept + extended).
        ink: {
          950: "#06070c",
          900: "#0a0b0f",
          800: "#141821",
          700: "#1c2230",
          600: "#2a3142",
        },
      },
      fontFamily: {
        // Wired up via next/font in layout.tsx (CSS variable --font-sans).
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      backgroundImage: {
        // Primary action gradient: green -> teal/cyan.
        "grad-primary": "linear-gradient(135deg, #39ff14 0%, #2dd4bf 55%, #22d3ee 100%)",
        "grad-text": "linear-gradient(120deg, #39ff14 0%, #2dd4bf 45%, #38bdf8 100%)",
        // Subtle page backdrop: layered radial glows over near-black.
        "app-radial":
          "radial-gradient(1200px 600px at 15% -10%, rgba(57,255,20,0.10), transparent 60%), radial-gradient(1000px 500px at 100% 0%, rgba(56,189,248,0.10), transparent 55%), radial-gradient(900px 700px at 50% 120%, rgba(45,212,191,0.08), transparent 60%)",
      },
      boxShadow: {
        glass: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 10px 30px -10px rgba(0,0,0,0.6)",
        "glow-green": "0 0 0 1px rgba(57,255,20,0.25), 0 8px 30px -8px rgba(57,255,20,0.35)",
        "glow-blue": "0 0 0 1px rgba(56,189,248,0.25), 0 8px 30px -8px rgba(56,189,248,0.35)",
        soft: "0 8px 24px -12px rgba(0,0,0,0.7)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
