/**
 * ESLint config for Studio Web (TypeScript + React + Hooks).
 * Note: Uses the classic .eslintrc format for broad editor/tooling support.
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true
  },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    tsconfigRootDir: __dirname,
    project: false,
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true }
  },
  settings: {
    react: { version: "detect" },
    "import/resolver": {
      node: { extensions: [".js", ".jsx", ".ts", ".tsx"] },
      typescript: { project: "." }
    }
  },
  plugins: [
    "@typescript-eslint",
    "react",
    "react-hooks",
    "import"
  ],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
    "plugin:import/recommended",
    "plugin:import/typescript"
  ],
  rules: {
    // General
    "no-console": ["warn", { allow: ["warn", "error"] }],
    "no-debugger": "warn",

    // Imports
    "import/order": ["warn", {
      "groups": ["builtin", "external", "internal", "parent", "sibling", "index", "object", "type"],
      "newlines-between": "always",
      "alphabetize": { order: "asc", caseInsensitive: true }
    }],
    "import/no-unresolved": "error",

    // TypeScript
    "@typescript-eslint/consistent-type-imports": ["warn", { prefer: "type-imports" }],
    "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    "@typescript-eslint/no-explicit-any": "off",

    // React
    "react/jsx-boolean-value": ["warn", "never"],
    "react/self-closing-comp": ["warn", { component: true, html: true }],
    "react/prop-types": "off", // using TS types instead
    "react/no-unknown-property": ["error", { ignore: ["css"] }],

    // Hooks
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn"
  },
  overrides: [
    {
      files: ["**/*.{test,spec}.ts?(x)"],
      env: { "vitest/globals": true, node: true },
      rules: {
        "no-console": "off"
      }
    },
    {
      files: ["**/*.d.ts"],
      rules: {
        "@typescript-eslint/no-unused-vars": "off"
      }
    }
  ],
  ignorePatterns: [
    "dist/",
    "build/",
    "coverage/",
    "node_modules/",
    "*.config.*",
    "public/"
  ]
};
