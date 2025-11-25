/**
 * ESLint configuration for a React + TypeScript dapp template.
 * - Strong but friendly defaults
 * - TS-aware rules (without requiring type-aware linting by default)
 * - React & Hooks best practices
 * - Import hygiene & ordering
 * - Clean unused imports automatically surfaced as warnings
 *
 * Tip: to enable type-aware rules, set parserOptions.project to './tsconfig.json'
 * and add "plugin:@typescript-eslint/recommended-requiring-type-checking" to extends.
 */
module.exports = {
  root: true,

  env: {
    browser: true,
    node: true,
    es2021: true,
  },

  parser: '@typescript-eslint/parser',

  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
    // For faster lint: keep project unset by default.
    // If you want type-aware linting, set this to ['./tsconfig.json'].
    project: false,
  },

  settings: {
    react: { version: 'detect' },
    // Resolve TS path aliases declared in tsconfig.json
    'import/resolver': {
      typescript: {
        alwaysTryTypes: true,
        project: ['tsconfig.json'],
      },
      node: {
        extensions: ['.js', '.jsx', '.ts', '.tsx'],
      },
    },
  },

  plugins: [
    '@typescript-eslint',
    'react',
    'react-hooks',
    'import',
    'jsx-a11y',
    'unused-imports',
  ],

  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'plugin:import/recommended',
    'plugin:jsx-a11y/recommended',
  ],

  ignorePatterns: [
    'dist/',
    'coverage/',
    'node_modules/',
    // Config files themselves
    '.eslintrc.cjs',
    '*.config.*',
  ],

  rules: {
    // --- General cleanliness & safety
    'no-console': ['warn', { allow: ['warn', 'error'] }],
    'no-debugger': 'warn',
    'prefer-const': 'warn',

    // --- TypeScript rules (let TS own unused vars)
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'warn',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      },
    ],
    '@typescript-eslint/explicit-module-boundary-types': 'off',

    // --- React specifics
    'react/react-in-jsx-scope': 'off', // New JSX transform
    'react/prop-types': 'off', // Using TS for types
    'react/self-closing-comp': 'warn',
    'react/jsx-boolean-value': ['warn', 'never'],

    // --- Hooks correctness
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',

    // --- Imports hygiene & ordering
    'import/no-unresolved': 'off', // TS resolver handles this
    'import/order': [
      'warn',
      {
        'newlines-between': 'always',
        groups: [
          ['builtin', 'external'],
          ['internal'],
          ['parent', 'sibling', 'index', 'object'],
          ['type'],
        ],
        alphabetize: { order: 'asc', caseInsensitive: true },
      },
    ],

    // Surface unused imports as warnings (nice DX with many editors)
    'unused-imports/no-unused-imports': 'warn',
    'unused-imports/no-unused-vars': [
      'warn',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
  },

  overrides: [
    // Unit tests (Vitest/Jest-like globals)
    {
      files: ['**/*.{test,spec}.{ts,tsx}'],
      env: { node: true, browser: true, jest: true },
      rules: {
        '@typescript-eslint/no-non-null-assertion': 'off',
      },
    },

    // Node-context config & scripts
    {
      files: [
        'vite.config.*',
        'vitest.config.*',
        'playwright.config.*',
        '*.config.{js,ts,cjs,mjs}',
        'scripts/**/*.{js,ts}',
      ],
      env: { node: true },
      rules: {
        // Configs/scripts often import devDeps
        'import/no-extraneous-dependencies': 'off',
      },
    },

    // E2E tests (Playwright)
    {
      files: ['e2e/**', 'test/e2e/**', 'tests/e2e/**'],
      env: { node: true },
    },
  ],
};
