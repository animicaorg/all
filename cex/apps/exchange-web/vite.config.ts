import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: parseInt(process.env.PORT || '5174'),
    host: process.env.HOST || '127.0.0.1',
    strictPort: true,
  },
});
