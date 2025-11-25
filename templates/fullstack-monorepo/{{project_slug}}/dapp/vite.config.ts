import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Vite uses import.meta.env.* for values beginning with VITE_.
// Common vars you might set in .env / .env.local:
//   VITE_RPC_URL=http://localhost:8545
//   VITE_CHAIN_ID=1337
//   VITE_SERVICES_URL=http://localhost:8787
//   VITE_PORT=5173
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const PORT = Number(env.VITE_PORT ?? 5173)

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    server: {
      host: true,
      port: PORT,
      strictPort: true,
      open: true,
      // COOP/COEP are helpful if you later add WASM/Workers (e.g., studio-wasm)
      headers: {
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Cross-Origin-Embedder-Policy': 'require-corp',
      },
    },
    preview: {
      host: true,
      port: PORT,
      strictPort: true,
    },
    build: {
      target: ['es2022', 'chrome100', 'safari15'],
      outDir: 'dist',
      sourcemap: true,
      rollupOptions: {
        output: {
          // Sensible chunking: keep React and SDK separate for better caching
          manualChunks(id) {
            if (id.includes('node_modules')) {
              if (id.includes('react')) return 'vendor-react'
              if (id.includes('@animica/sdk')) return 'vendor-animica'
              return 'vendor'
            }
          },
        },
      },
    },
    optimizeDeps: {
      // Ensure the browser-flavored SDK paths are prebundled
      include: [
        '@animica/sdk',
        '@animica/sdk/rpc/http',
        '@animica/sdk/rpc/ws',
      ],
      // Never try to bundle node-only deps into the browser build
      exclude: ['ws'],
    },
    define: {
      // Example: make app version available if desired
      __APP_VERSION__: JSON.stringify(env.npm_package_version ?? '0.0.0'),
    },
  }
})
