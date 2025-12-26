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

const hmrHost = process.env.VITE_HMR_HOST;
const hmrProtocol = process.env.VITE_HMR_PROTOCOL;
const hmrPort = process.env.VITE_HMR_PORT ? Number(process.env.VITE_HMR_PORT) : 3001;
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT ? Number(process.env.VITE_HMR_CLIENT_PORT) : undefined;
const hmrConfig =
  hmrHost || hmrProtocol || hmrClientPort || process.env.VITE_HMR_PORT
    ? {
        host: hmrHost,
        protocol: hmrProtocol,
        port: hmrPort,
        clientPort: hmrClientPort,
      }
    : undefined;

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
        target: 'http://127.0.0.1:8545',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/rpc/, '/rpc'),
      },
    },
    ...(hmrConfig ? { hmr: hmrConfig } : {}),
    watch: {
      usePolling: false,
    },
  },
});
