import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

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
    hmr: {
      protocol: 'ws',
      host: 'localhost',
      port: 3001,
      clientPort: 3001,
    },
    watch: {
      usePolling: false,
    },
  },
});
