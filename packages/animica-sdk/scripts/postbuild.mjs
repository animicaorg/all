// Stamp per-format package.json files so Node resolves each dist tree with
// the right module system regardless of the root "type" field.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const stamps = [
  [join(root, "dist", "cjs", "package.json"), { type: "commonjs" }],
  [join(root, "dist", "esm", "package.json"), { type: "module" }],
];

for (const [path, body] of stamps) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(body) + "\n");
}
