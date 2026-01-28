import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    port: parseInt(process.env.PORT || '5175'),
    host: process.env.HOST || '0.0.0.0',
    strictPort: false,
  },
});
