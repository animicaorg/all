import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

const adminApiProxyTarget =
  process.env.VITE_ADMIN_API_PROXY_TARGET ||
  process.env.ADMIN_API_URL ||
  'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    allowedHosts: ['admin.animica.org'],
    port: 5173,
    proxy: {
      '/admin/v1': {
        target: adminApiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
