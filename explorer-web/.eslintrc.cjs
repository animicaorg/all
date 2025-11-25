/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2021: true,
    node: true
  },
  settings: {
    react: {
      version: 'detect'
    },
    'import/resolver': {
      node: {
        extensions: ['.ts', '.tsx', '.js', '.jsx']
      }
    }
  },
  ignorePatterns: [
    'dist/',
    'coverage/',
    'node_modules/',
    // Vite artifacts
    '*.config.*.timestamp-*',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    // If you later enable type-aware rules, point to your tsconfig here:
    // project: ['./tsconfig.json']
  },
  plugins: [
    '@typescript-eslint',
    'react',
    'react-hooks',
    'import',
    'unused-imports',
    'jsx-a11y'
  ],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'plugin:react-hooks/recommended',
    'plugin:import/recommended',
    'plugin:import/typescript',
    'plugin:jsx-a11y/recommended',
    // keeps rules compatible with Prettier formatting (if you add Prettier)
    'prettier'
  ],
  rules: {
    // React 17+ JSX transform
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',

    // Hooks best practices
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',

    // Imports
    'import/no-unresolved': 'error',
    'import/order': ['warn', {
      'groups': ['builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
      'newlines-between': 'always',
      'alphabetize': { order: 'asc', caseInsensitive: true }
    }],

    // TypeScript strictness (tune as your codebase matures)
    '@typescript-eslint/consistent-type-imports': ['warn', { prefer: 'type-imports' }],
    '@typescript-eslint/no-unused-vars': 'off', // handled by unused-imports
    'unused-imports/no-unused-imports': 'warn',
    'unused-imports/no-unused-vars': [
      'warn',
      { vars: 'all', varsIgnorePattern: '^_', args: 'after-used', argsIgnorePattern: '^_' }
    ],

    // A11y: keep recommended defaults; add any project-specific adjustments here
    'jsx-a11y/anchor-is-valid': 'warn',
  },
  overrides: [
    // Test files (Vitest / Playwright)
    {
      files: ['**/*.test.ts', '**/*.test.tsx', '**/*.spec.ts', '**/*.spec.tsx', 'playwright.config.ts'],
      env: { node: true, browser: true },
      plugins: ['vitest'],
      extends: ['plugin:vitest/recommended'],
      rules: {
        // tests often have dev-only imports or console usage
        'no-console': 'off'
      }
    },
    // Vite config and other node-only scripts
    {
      files: ['vite.config.ts', 'scripts/**/*.ts', '*.cjs', '*.cts'],
      env: { node: true },
      rules: {
        'import/no-extraneous-dependencies': 'off'
      }
    }
  ],
  globals: {
    // defined in Vite config via define:
    __APP_ENV__: 'readonly',
    __APP_BUILD_TIME__: 'readonly'
  }
};
