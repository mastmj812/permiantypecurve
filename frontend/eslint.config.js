// ESLint 9 flat config. Migrated from the legacy .eslintrc.cjs, which
// ESLint 9 no longer reads (it defaults to flat config). Mirrors the
// previous setup: eslint:recommended + typescript-eslint
// recommended-type-checked, react-hooks, react-refresh, and
// consistent-type-imports.
//
// Linting is scoped to src/** to match tsconfig.json's `include: ["src"]`
// — type-checked rules require every linted file to be part of the TS
// project, so root config files (vite.config.ts, this file) are left
// out rather than erroring as "not found by the project service".

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommendedTypeChecked,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
      parserOptions: {
        project: ["./tsconfig.json"],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
);
