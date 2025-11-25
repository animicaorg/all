/* eslint-env node */
module.exports = {
  root: true,
  ignorePatterns: [
    "dist/",
    "dist-manifests/",
    "node_modules/",
    "coverage/",
    "playwright-report/",
    "vendor/",
    "public/*.html",
    "src/background/pq/wasm/*.wasm"
  ],
  env: {
    browser: true,
    es2022: true,
    worker: true
  },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    project: ["./tsconfig.json"],
    tsconfigRootDir: __dirname,
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true }
  },
  plugins: [
    "@typescript-eslint",
    "react",
    "react-hooks",
    "import",
    "unused-imports"
  ],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:@typescript-eslint/recommended-requiring-type-checking",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
    "plugin:import/recommended",
    "plugin:import/typescript",
    "prettier"
  ],
  settings: {
    react: { version: "detect" },
    "import/resolver": {
      typescript: { project: ["./tsconfig.json"] }
    }
  },
  globals: {
    chrome: "readonly",     // MV3 global
    browser: "readonly",    // Firefox polyfill
    __DEV__: "readonly"     // defined in Vite
  },
  rules: {
    // TS/strictness
    "@typescript-eslint/no-floating-promises": ["error", { ignoreVoid: true }],
    "@typescript-eslint/no-misused-promises": ["error", { checksVoidReturn: { attributes: false } }],
    "@typescript-eslint/explicit-function-return-type": "off",
    "@typescript-eslint/no-explicit-any": "off",

    // React
    "react/react-in-jsx-scope": "off",
    "react/prop-types": "off",

    // Imports
    "import/order": ["warn", {
      "groups": ["builtin", "external", "internal", "parent", "sibling", "index", "object", "type"],
      "newlines-between": "always",
      "alphabetize": { order: "asc", caseInsensitive: true }
    }],

    // Cleanups
    "unused-imports/no-unused-imports": "warn",
    "no-console": ["warn", { allow: ["warn", "error"] }]
  },

  overrides: [
    // Background service worker runs in a serviceworker context, not window
    {
      files: ["src/background/**/*.{ts,tsx}"],
      env: {
        worker: true,
        browser: true,
        es2022: true
      },
      rules: {
        "no-restricted-globals": "off"
      }
    },
    // Content scripts & in-page bridges
    {
      files: ["src/content/**/*.{ts,tsx}", "src/provider/**/*.{ts,tsx}"],
      env: { browser: true, es2022: true }
    },
    // MV3-compatible workers
    {
      files: ["src/workers/**/*.{ts,tsx}"],
      env: { worker: true, browser: true, es2022: true }
    },
    // Node-only build/dev scripts
    {
      files: ["scripts/**/*.{ts,js}", "*.config.{ts,js}", "*.cjs"],
      env: { node: true, es2022: true },
      parserOptions: { sourceType: "module" }
    },
    // Tests (vitest + playwright)
    {
      files: ["test/**/*.{ts,tsx}", "**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}"],
      env: {
        browser: true,
        es2022: true
      },
      globals: {
        vi: "readonly",
        describe: "readonly",
        it: "readonly",
        test: "readonly",
        expect: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly"
      }
    }
  ]
};
