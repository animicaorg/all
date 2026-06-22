import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import { fileURLToPath, URL } from "node:url";

// The broker (studio-host) serves the built app and the /api/ide/* routes on the
// same origin in production. In dev we proxy /api and the websocket endpoints to
// a locally-running broker so there's no CORS and cookies flow.
const BROKER = process.env.BROKER_URL || "http://127.0.0.1:8123";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": { target: BROKER, changeOrigin: true, ws: true },
      "/desktop": { target: BROKER, changeOrigin: true },
      "/go": { target: BROKER, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
    chunkSizeWarningLimit: 1200,
  },
});
