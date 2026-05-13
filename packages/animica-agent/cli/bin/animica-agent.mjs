#!/usr/bin/env node
/**
 * animica-agent CLI entry.
 *
 * Thin shim: defers everything to the compiled CLI module. Keeping the bin
 * minimal means `npm install -g animica-agent` only needs a working dist/.
 */
import { run } from "../dist/index.js";

run(process.argv.slice(2))
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    process.stderr.write(`[animica-agent] fatal: ${err?.stack ?? err}\n`);
    process.exit(2);
  });
