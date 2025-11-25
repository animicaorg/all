import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
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
  server: { host: true, port: 3001 },
});
