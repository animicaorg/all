import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { existsSync } from "node:fs";

const envLocalPath = path.resolve(__dirname, ".env.local");
if (existsSync(envLocalPath)) {
  throw new Error(
    [
      "explorer-web no longer supports `.env.local`.",
      "Rename the file to `.env` so configuration (RPC URLs, chain ID, etc.) comes from a single source.",
    ].join(" ")
  );
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      ws: path.resolve(__dirname, "src/shims/ws.ts"),
    },
  },
  optimizeDeps: {
    exclude: ["ws"],
  },
  define: {
    // some deps read process.env; keep it defined in browser to avoid crashes
    "process.env": {}
  },
  server: { 
    host: true, 
    port: 3001,
    allowedHosts: [
      "explorer.animica.org",
      "localhost",
      ".localhost",
      "127.0.0.1",
      "::1",
    ],
    proxy: {
      '/rpc': {
        target: 'https://rpc.animica.org',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/rpc/, '/rpc'),
      },
    },
    hmr: {
      // Use environment variable if set, otherwise default to localhost
      // In production/containers, set VITE_HMR_HOST to match your setup
      host: process.env.VITE_HMR_HOST || 'localhost',
      port: 3001,
    },
    watch: {
      usePolling: false,
    },
  },
});
