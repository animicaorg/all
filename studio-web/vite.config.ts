import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import wasm from "vite-plugin-wasm";
import topLevelAwait from "vite-plugin-top-level-await";

// Vite config for React + WASM (Pyodide) + Web Workers
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const RPC = env.VITE_RPC_URL || "http://localhost:8545";
  const SERVICES = env.VITE_SERVICES_URL || "http://localhost:8787";

  return {
    plugins: [react(), wasm(), topLevelAwait()],
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version)
    },
    resolve: {
      alias: { "@": "/src" }
    },
    server: {
      port: 5173,
      strictPort: true,
      open: true,
      proxy: {
        // JSON-RPC HTTP
        "/rpc": {
          target: RPC,
          changeOrigin: true,
          secure: false
        },
        // JSON-RPC WS (assumes same base; override with full ws URL if needed)
        "/ws": {
          target: RPC.replace(/^http/, "ws"),
          ws: true,
          changeOrigin: true,
          secure: false
        },
        // Studio services (deploy/verify/faucet/simulate)
        "/services": {
          target: SERVICES,
          changeOrigin: true,
          secure: false
        }
      }
    },
    worker: {
      format: "es",
      plugins: () => [wasm(), topLevelAwait()]
    },
    build: {
      target: "es2022",
      sourcemap: true,
      assetsInlineLimit: 0, // keep .wasm as separate files
      rollupOptions: {
        output: {
          manualChunks: {
            "monaco-editor": ["monaco-editor"]
          }
        }
      }
    },
    optimizeDeps: {
      esbuildOptions: { target: "es2022" }
    },
    json: { stringify: true }
  };
});
