// Post-build fixups for the dual ESM/CJS package:
//
// 1. Stamp per-format package.json files so Node resolves each dist tree with
//    the right module system regardless of the root "type" field.
//
// 2. Emit a CJS-flavored declaration tree (dist/types-cjs/*.d.cts) mirrored
//    from dist/types/*.d.ts. The root package.json has "type": "module", so
//    the .d.ts files are ESM-flavored; without a .d.cts tree, TypeScript
//    consumers on module/moduleResolution node16/nodenext using require()
//    fail with TS1479 ("masquerading as ESM"). Relative import specifiers
//    inside the copies are rewritten ./x.js -> ./x.cjs so they resolve to
//    the sibling .d.cts declarations.
import {
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

// --- 1. per-format package.json stamps -------------------------------------

const stamps = [
  [join(root, "dist", "cjs", "package.json"), { type: "commonjs" }],
  [join(root, "dist", "esm", "package.json"), { type: "module" }],
];

for (const [path, body] of stamps) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(body) + "\n");
}

// --- 2. dist/types-cjs/*.d.cts ---------------------------------------------

const typesDir = join(root, "dist", "types");
const typesCjsDir = join(root, "dist", "types-cjs");

rmSync(typesCjsDir, { recursive: true, force: true });

/**
 * Rewrite relative specifiers ending in .js to .cjs in every quoted string
 * that looks like a module specifier (covers `from "./x.js"`,
 * `import("./x.js")` and `export ... from "./x.js"`).
 */
function toCjsSpecifiers(source) {
  return source.replace(
    /(["'])(\.\.?\/[^"']*)\.js\1/g,
    (_m, quote, spec) => `${quote}${spec}.cjs${quote}`
  );
}

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else yield path;
  }
}

for (const srcPath of walk(typesDir)) {
  const rel = relative(typesDir, srcPath);
  if (!rel.endsWith(".d.ts")) continue;
  const outPath = join(typesCjsDir, rel.slice(0, -".d.ts".length) + ".d.cts");
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, toCjsSpecifiers(readFileSync(srcPath, "utf8")));
}
