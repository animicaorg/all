import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    allowedHosts: ['trade.animica.org', '144.126.133.21', 'localhost'],
    port: parseInt(process.env.PORT || '5175'),
    host: process.env.HOST || '0.0.0.0',
    strictPort: false,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_PROXY_TARGET || 'http://127.0.0.1:3000',
        changeOrigin: true,
      },
      '/ws': {
        target: process.env.VITE_DEV_API_PROXY_TARGET || 'http://127.0.0.1:3000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
