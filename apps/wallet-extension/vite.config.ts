import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';
import { copyFileSync, existsSync, mkdirSync } from 'fs';

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'copy-manifest',
      writeBundle() {
        const dist = resolve(__dirname, 'dist');
        mkdirSync(dist, { recursive: true });

        for (const file of [
          'manifest.json',
          'icon-16.png',
          'icon-32.png',
          'icon-48.png',
          'icon-128.png'
        ]) {
          const source = resolve(__dirname, file);
          if (existsSync(source)) {
            copyFileSync(source, resolve(dist, file));
          }
        }
      }
    }
  ],
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        popup: resolve(__dirname, 'src/ui/popup.html'),
        background: resolve(__dirname, 'src/background/index.ts'),
        content: resolve(__dirname, 'src/content/index.ts'),
        provider: resolve(__dirname, 'src/provider/index.ts'),
      },
      output: {
        entryFileNames: (chunkInfo) => {
          if (chunkInfo.name === 'background') return 'background.js';
          if (chunkInfo.name === 'content') return 'content.js';
          if (chunkInfo.name === 'provider') return 'provider.js';
          return '[name].[hash].js';
        },
        chunkFileNames: 'chunks/[name].[hash].js',
        assetFileNames: 'assets/[name].[ext]'
      }
    }
  }
});
