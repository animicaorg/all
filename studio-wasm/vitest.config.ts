import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      // Allow tests to import like: import('/src/...') to mirror the app/browser paths.
      '/src': r('./src'),
      // Handy if a unit test wants to import example assets directly (most use ?raw/fetch).
      '/examples': r('./examples'),
    },
  },
  test: {
    // Node is fine for unit tests; Pyodide-dependent tests skip gracefully when assets aren't available.
    environment: 'node',
    globals: true,
    setupFiles: ['test/setup.ts'],
    include: ['test/unit/**/*.test.ts'],
    watchExclude: ['**/vendor/**'],
    // Speed + stability
    pool: 'threads',
    isolate: true,
    // Nicer CI output; local runs keep default reporter
    reporters: process.env.CI ? ['dot', 'junit'] : ['default'],
    outputFile: process.env.CI ? { junit: 'junit.xml' } : undefined,
    // Basic coverage
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      reportsDirectory: 'coverage',
      exclude: [
        'test/**',
        'playwright.config.ts',
        'vite.config.ts',
        'tsconfig.json',
        'src/types/**',
      ],
    },
    // Give Pyodide a little extra time in unit tests that actually boot it.
    testTimeout: 120_000,
    hookTimeout: 60_000,
  },
});
