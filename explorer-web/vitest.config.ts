import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';
// import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
  plugins: [react()], // tsconfigPaths() removed - using vite resolve.alias instead
  resolve: {
    alias: {
      '@': '/src',
      ws: path.resolve(__dirname, 'src/shims/ws.ts'),
    },
  },
  esbuild: { target: 'es2020' },
  test: {
    environment: 'jsdom',
    setupFiles: ['test/setup.ts'],
    include: ['test/unit/**/*.{test,spec}.ts?(x)'],
    exclude: ['test/e2e/**', 'node_modules/**', 'dist/**'],
    globals: true,
    testTimeout: 20_000,
    hookTimeout: 10_000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: 'coverage',
      exclude: [
        'test/**',
        'playwright.config.ts',
        'vite.config.ts',
        '**/*.d.ts',
      ],
    },
  },
});
