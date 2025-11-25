/**
 * ESLint config for Astro + TypeScript (+ optional React islands).
 * Uses legacy .eslintrc for compatibility across editors/CI.
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    node: true,
    es2021: true
  },
  ignorePatterns: [
    'dist/',
    'out/',
    'node_modules/',
    '.astro/',
    '.vercel/',
    '.netlify/'
  ],
  extends: [
    'eslint:recommended',
    'plugin:astro/recommended',
    // TypeScript rules are applied via overrides below as well (for TS-aware parsing)
    'plugin:@typescript-eslint/recommended'
  ],
  plugins: ['@typescript-eslint', 'import', 'astro'],
  settings: {
    // Enable TS support inside .astro files
    'astro/typescript': true
  },
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module'
  },

  overrides: [
    // --- Astro files ---
    {
      files: ['**/*.astro'],
      // Parse <script> in .astro via TS parser
      parser: 'astro-eslint-parser',
      parserOptions: {
        parser: '@typescript-eslint/parser',
        extraFileExtensions: ['.astro'],
        ecmaVersion: 'latest',
        sourceType: 'module'
      },
      rules: {
        // Example: encourage accessible images
        'astro/no-set-html-directive': 'warn'
      }
    },

    // --- TypeScript files ---
    {
      files: ['**/*.{ts,tsx}'],
      parser: '@typescript-eslint/parser',
      parserOptions: {
        // Set to true only if you add a project tsconfig for type-aware rules
        // project: ['./tsconfig.json'],
        ecmaVersion: 'latest',
        sourceType: 'module'
      },
      extends: ['plugin:@typescript-eslint/recommended'],
      rules: {
        '@typescript-eslint/no-unused-vars': [
          'warn',
          { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }
        ],
        '@typescript-eslint/consistent-type-imports': 'warn',
        'import/order': [
          'warn',
          {
            'newlines-between': 'always',
            alphabetize: { order: 'asc', caseInsensitive: true },
            groups: [
              'builtin',
              'external',
              'internal',
              ['parent', 'sibling', 'index'],
              'object',
              'type'
            ]
          }
        ]
      }
    },

    // --- JavaScript files ---
    {
      files: ['**/*.{js,jsx,mjs,cjs}'],
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module'
      }
    }
  ],

  rules: {
    // Reasonable defaults
    'no-console': ['warn', { allow: ['warn', 'error'] }],
    'no-debugger': 'warn',

    // Turn off to avoid false positives unless resolver plugins are added
    'import/no-unresolved': 'off'
  }
};
