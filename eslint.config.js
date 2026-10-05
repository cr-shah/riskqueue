import js from "@eslint/js";
import globals from "globals";

export default [
  js.configs.recommended,
  { files: ["website/**/*.js"], languageOptions: { globals: globals.browser } },
  {
    files: ["tests/web/**/*.js", "scripts/*.mjs", "*.js"],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
];
