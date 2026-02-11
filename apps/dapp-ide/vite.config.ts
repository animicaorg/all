import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import wasm from "vite-plugin-wasm";
import topLevelAwait from "vite-plugin-top-level-await";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const RPC = env.VITE_RPC_URL || "http://localhost:8545";

  return {
    plugins: [react(), wasm(), topLevelAwait()],
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version || "0.1.0"),
      "process.env": {},
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      port: 5174,
      strictPort: false,
      open: true,
      proxy: {
        "/rpc": {
          target: RPC,
          changeOrigin: true,
          secure: false,
        },
        "/ws": {
          target: RPC.replace(/^http/, "ws"),
          ws: true,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    worker: {
      format: "es",
      plugins: () => [wasm(), topLevelAwait()],
    },
    build: {
      target: "es2022",
      sourcemap: true,
      assetsInlineLimit: 0,
      rollupOptions: {
        output: {
          manualChunks: {
            "monaco-editor": ["monaco-editor"],
            "react-vendor": ["react", "react-dom", "react-router-dom"],
          },
        },
      },
    },
    optimizeDeps: {
      esbuildOptions: { target: "es2022" },
    },
  };
});
