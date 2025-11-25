/**
 * ESLint config for @animica/studio-wasm
 * - TypeScript-first
 * - Browser + Worker runtime for the library
 * - Node runtime for build/scripts/bench configs
 * - Vitest + Playwright test files
 *
 * Most dependencies are expected at the monorepo root. If you lint this
 * package standalone, ensure you install:
 *   eslint @typescript-eslint/parser @typescript-eslint/eslint-plugin
 *   eslint-plugin-import eslint-plugin-unused-imports eslint-config-prettier
 */

module.exports = {
  root: false, // monorepo likely owns the root config; this file augments for the package
  env: {
    es2020: true,
    browser: true,
    worker: true
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    project: false,
    sourceType: 'module',
    ecmaVersion: 'latest'
  },
  plugins: [
    '@typescript-eslint',
    'import',
    'unused-imports'
  ],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:import/recommended',
    'plugin:import/typescript',
    'plugin:prettier/recommended' // keeps eslint and prettier from fighting
  ],
  settings: {
    'import/resolver': {
      // Let eslint-import-resolver-typescript pick up tsconfig paths (vite uses "bundler" but this is fine for lint)
      typescript: {
        alwaysTryTypes: true,
        project: ['./tsconfig.json']
      }
    }
  },
  rules: {
    // General hygiene
    'no-duplicate-imports': 'error',
    'prefer-const': ['warn', { destructuring: 'all' }],
    'no-console': ['warn', { allow: ['warn', 'error'] }],

    // Imports
    'import/no-unresolved': 'error',
    'import/order': ['warn', {
      'alphabetize': { order: 'asc', caseInsensitive: true },
      'newlines-between': 'always',
      'groups': [
        'builtin',
        'external',
        'internal',
        ['parent', 'sibling', 'index'],
        'object',
        'type'
      ]
    }],

    // Unused
    'unused-imports/no-unused-imports': 'warn',
    'unused-imports/no-unused-vars': [
      'warn',
      { args: 'after-used', argsIgnorePattern: '^_', varsIgnorePattern: '^_' }
    ],

    // TS-specific
    '@typescript-eslint/ban-ts-comment': ['warn', { 'ts-ignore': 'allow-with-description' }],
    '@typescript-eslint/no-explicit-any': 'off',
    '@typescript-eslint/no-unused-vars': 'off' // superseded by unused-imports
  },

  overrides: [
    // Node contexts: build & scripts & config
    {
      files: [
        'vite.config.ts',
        'playwright.config.ts',
        'vitest.config.ts',
        'scripts/**/*.{ts,js}',
        'bench/**/*.ts'
      ],
      env: { node: true, browser: false, worker: false },
      rules: {
        'no-console': 'off'
      }
    },

    // Test files (Vitest + Playwright)
    {
      files: ['test/**/*.{ts,tsx}'],
      env: { node: true },
      extends: ['plugin:vitest/recommended'],
      plugins: ['vitest'],
      rules: {
        'no-console': 'off'
      }
    },

    // Example/demo sources can be more permissive
    {
      files: ['examples/**/*.{ts,tsx}'],
      rules: {
        'no-console': 'off',
        '@typescript-eslint/no-explicit-any': 'off'
      }
    },

    // Worker entry: ensure no Node usage sneaks in
    {
      files: ['src/worker/**/*.ts'],
      env: { worker: true, browser: true, node: false },
      rules: {
        'import/no-nodejs-modules': 'off' // if using plugin: you can enforce no Node builtins here
      }
    }
  ],
  ignorePatterns: [
    'dist/',
    'vendor/',
    'node_modules/',
    // Generated or external artifacts
    '*.d.ts'
  ]
};
