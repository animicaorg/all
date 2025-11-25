/**
 * Animica shared ESLint config (template)
 * ---------------------------------------
 * Rendered into new repos by the templates engine.
 *
 * Goals:
 * - Strong, modern baseline for TypeScript + React + Node libs/apps
 * - Prettier integration for formatting
 * - Sensible import ordering & unused-import cleanup
 * - Test/E2E overrides for Jest/Testing Library/Playwright
 *
 * Template variables (resolved at render time):
 *   {{ tsconfig_path | default('tsconfig.json') }}
 */

const OFF  = 'off';
const WARN = 'warn';
const ERROR = 'error';

/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,

  env: {
    es2022: true,
    browser: true,
    node: true,
  },

  parser: '@typescript-eslint/parser',

  parserOptions: {
    sourceType: 'module',
    ecmaVersion: 'latest',
    tsconfigRootDir: __dirname,
    // Enable type-aware rules when a project is present.
    project: ['{{ tsconfig_path | default("tsconfig.json") }}'],
  },

  settings: {
    react: { version: 'detect' },
    // Better path resolution for import/* rules
    'import/resolver': {
      typescript: { project: '{{ tsconfig_path | default("tsconfig.json") }}' },
      node: {
        extensions: ['.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx'],
      },
    },
  },

  plugins: [
    '@typescript-eslint',
    'react',
    'react-hooks',
    'jsx-a11y',
    'import',
    'unused-imports',
  ],

  extends: [
    'eslint:recommended',

    // TypeScript
    'plugin:@typescript-eslint/recommended',
    'plugin:@typescript-eslint/recommended-requiring-type-checking',

    // React
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',

    // A11y (web UIs)
    'plugin:jsx-a11y/recommended',

    // Imports hygiene
    'plugin:import/recommended',
    'plugin:import/typescript',

    // Keep last so it can disable conflicting stylistic rules
    'plugin:prettier/recommended',
  ],

  rules: {
    // --- Core sanity ---------------------------------------------------------
    'no-console': [WARN, { allow: ['warn', 'error'] }],
    'no-debugger': WARN,
    'no-alert': WARN,
    'prefer-const': [WARN, { destructuring: 'all' }],
    'object-shorthand': [WARN, 'always'],

    // --- Imports hygiene -----------------------------------------------------
    'import/no-unresolved': ERROR,
    'import/newline-after-import': [WARN, { count: 1 }],
    'import/no-duplicates': ERROR,
    'import/order': [
      WARN,
      {
        groups: [
          'builtin', 'external', 'internal',
          'parent', 'sibling', 'index', 'object', 'type'
        ],
        'newlines-between': 'always',
        alphabetize: { order: 'asc', caseInsensitive: true },
        pathGroupsExcludedImportTypes: ['builtin'],
      },
    ],

    // Remove dead imports automatically (editor plugin can fix)
    'unused-imports/no-unused-imports': WARN,
    // Keep unused args when prefixed with _
    '@typescript-eslint/no-unused-vars': [
      WARN,
      { vars: 'all', args: 'after-used', ignoreRestSiblings: true, argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],

    // --- TypeScript strictness ----------------------------------------------
    '@typescript-eslint/consistent-type-imports': [WARN, { prefer: 'type-imports' }],
    '@typescript-eslint/no-explicit-any': [WARN, { ignoreRestArgs: false }],
    '@typescript-eslint/ban-ts-comment': [
      WARN,
      {
        'ts-expect-error': 'allow-with-description',
        'ts-ignore': true,
        'ts-nocheck': true,
        'ts-check': false,
        minimumDescriptionLength: 10,
      },
    ],
    '@typescript-eslint/no-misused-promises': [
      ERROR,
      { checksVoidReturn: { attributes: false } },
    ],
    '@typescript-eslint/require-await': OFF, // often noisy with async interfaces
    '@typescript-eslint/explicit-module-boundary-types': OFF,

    // --- React specifics -----------------------------------------------------
    'react/react-in-jsx-scope': OFF, // Not needed with modern JSX transforms
    'react/prop-types': OFF,         // We use TypeScript for types
    'react/no-unknown-property': [ERROR, { ignore: ['css'] }], // Allow css prop for CSS-in-JS libraries

    // --- A11y tweaks ---------------------------------------------------------
    'jsx-a11y/no-autofocus': [WARN, { ignoreNonDOM: true }],
  },

  overrides: [
    // JS config/build files & scripts run in Node context
    {
      files: [
        '**/*.config.{js,cjs,mjs,ts}',
        '**/scripts/**',
        '**/tools/**',
      ],
      env: { node: true },
      rules: {
        '@typescript-eslint/no-var-requires': OFF,
        'import/no-extraneous-dependencies': OFF,
      },
    },

    // Unit tests (Jest + Testing Library)
    {
      files: [
        '**/*.{test,spec}.{js,jsx,ts,tsx}',
        '**/__tests__/**',
        'test/**',
        'tests/**',
      ],
      env: { jest: true, node: true, browser: true },
      plugins: ['jest', 'testing-library', 'jest-dom'],
      extends: [
        'plugin:jest/recommended',
        'plugin:testing-library/react',
        'plugin:jest-dom/recommended',
      ],
      rules: {
        '@typescript-eslint/no-explicit-any': OFF,
        '@typescript-eslint/no-unsafe-member-access': OFF,
        '@typescript-eslint/no-unsafe-assignment': OFF,
        'no-console': OFF,
      },
    },

    // Playwright E2E tests
    {
      files: [
        '**/*.e2e.{ts,tsx,js}',
        '**/e2e/**',
        '**/playwright.{ts,js}',
        '**/playwright.config.{ts,js}',
      ],
      env: { 'playwright/playwright-test': true },
      plugins: ['playwright'],
      extends: ['plugin:playwright/recommended'],
      rules: {
        '@typescript-eslint/no-floating-promises': OFF,
        'no-console': OFF,
      },
    },

    // Storybook stories (if present)
    {
      files: ['**/*.stories.@(ts|tsx|js|jsx|mdx)'],
      rules: {
        'import/no-default-export': OFF,
        'react/jsx-props-no-spreading': OFF,
      },
    },

    // Generated files (codegen) — keep linting light
    {
      files: ['**/generated/**', '**/_generated/**'],
      rules: {
        'unused-imports/no-unused-imports': OFF,
        '@typescript-eslint/no-unused-vars': OFF,
        '@typescript-eslint/ban-ts-comment': OFF,
      },
    },
  ],

  ignorePatterns: [
    'dist/',
    'build/',
    'coverage/',
    'node_modules/',
    '.eslintrc.cjs', // this file
    '*.min.js',
    '**/vendor/**',
    'public/',
    // repo-specific caches/artifacts frequently seen in Animica projects
    '.mypy_cache/',
    '.pytest_cache/',
    '.ruff_cache/',
    'tests/reports/',
    'tests/artifacts/',
  ],

  reportUnusedDisableDirectives: true,
};
