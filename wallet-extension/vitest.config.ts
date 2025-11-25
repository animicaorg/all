import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@noble/hashes': path.resolve(__dirname, 'src/polyfills/noble'),
    },
  },
  test: {
    // Unit tests live under test/unit; E2E is handled by Playwright separately
    include: ['test/unit/**/*.test.{ts,tsx}'],
    exclude: ['test/e2e/**', 'dist/**', 'node_modules/**'],

    // JSDOM lets React components and DOM APIs work in tests
    environment: 'jsdom',
    globals: true,

    // Make test runs predictable & clean
    restoreMocks: true,
    clearMocks: true,
    mockReset: true,

    // Coverage (v8): adjust include/exclude as needed
    coverage: {
      provider: 'v8',
      reportsDirectory: 'coverage',
      reporter: ['text', 'html', 'lcov'],
      exclude: [
        'test/**',
        'scripts/**',
        'dist/**',
        'public/**',
        'src/background/pq/wasm/*.wasm',
        'src/**/types.ts',
        'src/**/index.ts', // re-export barrels (usually small)
      ],
    },
  },
});
