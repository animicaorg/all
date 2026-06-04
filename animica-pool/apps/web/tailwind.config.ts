import type { Config } from "tailwindcss";
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        neon: { green: "#39ff14", violet: "#8b5cf6", blue: "#38bdf8" },
        ink: { 950: "#06070c", 900: "#0c0e15", 800: "#141821", 700: "#1c2230", 600: "#2a3142" },
      },
    },
  },
  plugins: [],
};
export default config;
