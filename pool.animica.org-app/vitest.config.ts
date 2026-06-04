import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    // Match tsconfig paths (most specific first).
    alias: {
      "@/lib": r("./src/lib"),
      "@/server": r("./src/server"),
      "@/components": r("./src/components"),
      "@": r("./src"),
    },
  },
  test: {
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    // DB tests share one Postgres; run files serially to keep them isolated.
    fileParallelism: false,
    hookTimeout: 30000,
    testTimeout: 30000,
  },
});
