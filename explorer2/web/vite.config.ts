import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const hmrHost = process.env.VITE_HMR_HOST
const hmrProtocol = process.env.VITE_HMR_PROTOCOL
const hmrPort = process.env.VITE_HMR_PORT ? Number(process.env.VITE_HMR_PORT) : 3001
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT ? Number(process.env.VITE_HMR_CLIENT_PORT) : undefined
const hmrConfig =
  hmrHost || hmrProtocol || hmrClientPort || process.env.VITE_HMR_PORT
    ? {
        host: hmrHost,
        protocol: hmrProtocol,
        port: hmrPort,
        clientPort: hmrClientPort
      }
    : undefined

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3001,
    allowedHosts: [
      'explorer.animica.org',
      'localhost',
      '.localhost',
      '127.0.0.1',
      '::1'
    ],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8081',
        changeOrigin: true
      }
    },
    ...(hmrConfig ? { hmr: hmrConfig } : {})
  }
})
