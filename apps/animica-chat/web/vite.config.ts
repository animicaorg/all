import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:4400',
        changeOrigin: true,
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
