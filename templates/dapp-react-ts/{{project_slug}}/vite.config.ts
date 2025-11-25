import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tsconfigPaths from 'vite-tsconfig-paths';

// Vite config tuned for a dapp:
// - React + TS + path aliases from tsconfig
// - Sensible dev server defaults (CORS on, auto-open)
// - Small vendor/code-splitting for faster loads
// - Exposes build-time constants for RPC/ChainId/version
// - Vitest config colocated for convenience
export default defineConfig(({ mode }) => {
  // Load only vars prefixed with VITE_
  const env = loadEnv(mode, process.cwd(), 'VITE_');

  // Defaults are friendly to local devnets
  const RPC_URL = env.VITE_RPC_URL ?? 'http://127.0.0.1:8545';
  const CHAIN_ID = Number(env.VITE_CHAIN_ID ?? '1337');

  return {
    plugins: [
      react({
        // Good DX: fast refresh & JSX runtime
        jsxImportSource: 'react',
        include: '**/*.{jsx,tsx}',
      }),
      tsconfigPaths(),
    ],

    server: {
      port: 5173,
      strictPort: false,
      open: true,
      cors: true,
      // Helpful proxy example (uncomment and edit to use):
      // proxy: {
      //   '/rpc': { target: RPC_URL, changeOrigin: true, rewrite: p => p.replace(/^\/rpc/, '') }
      // }
    },

    preview: {
      port: 4173,
      cors: true,
    },

    // Build for modern browsers; keep sourcemaps for easier debugging
    build: {
      target: 'es2020',
      sourcemap: true,
      outDir: 'dist',
      assetsDir: 'assets',
      rollupOptions: {
        output: {
          // Split bundles to keep first paint fast
          manualChunks: {
            react: ['react', 'react-dom'],
            // If you use the SDK in your template, this keeps it isolated
            sdk: ['@animica/sdk'],
          },
        },
      },
      chunkSizeWarningLimit: 800,
    },

    // Eager-optimize common deps to speed up dev server cold start
    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        '@animica/sdk',
        '@animica/sdk/rpc/http',
        '@animica/sdk/wallet/mnemonic',
      ],
    },

    // Useful build-time constants your app can read
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version),
      __RPC_URL__: JSON.stringify(RPC_URL),
      __CHAIN_ID__: JSON.stringify(CHAIN_ID),
    },

    // Web Worker behavior (handy if you later offload ABI/encode or WASM)
    worker: {
      format: 'es',
    },

    // Vitest colocated config (override in vitest.config.ts if you prefer)
    test: {
      environment: 'jsdom',
      globals: true,
      css: true,
      setupFiles: ['./test/setup.ts'],
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      coverage: {
        reporter: ['text', 'html'],
        reportsDirectory: 'coverage',
      },
    },
  };
});
