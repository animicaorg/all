import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  // Make "@/..." imports work in tests.
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['test/setup.ts'],

    include: ['test/unit/**/*.test.{ts,tsx}'],
    exclude: ['node_modules', 'dist', '.{idea,git,cache,output,temp}'],

    // Helpful defaults locally; richer reports on CI.
    reporters: process.env.CI ? ['default', 'junit'] : ['default'],
    outputFile: process.env.CI ? { junit: 'test-results/vitest-junit.xml' } : undefined,

    // Reasonable timeouts for browser-ish code paths.
    testTimeout: 30_000,
    hookTimeout: 30_000,

    // Transform target compatible with modern browsers/node LTS.
    deps: {
      inline: [
        // Add packages that ship ESM without CJS for consistent transforms if needed.
        // Example: '@animica/sdk', 'studio-wasm'
      ],
    },

    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      reportsDirectory: 'coverage',
      exclude: [
        'test/**/*',
        'src/**/__mocks__/**',
        'src/**/*.d.ts',
        '**/*.config.*',
      ],
    },
  },
});
