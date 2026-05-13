#!/usr/bin/env node
import { run } from "../dist/index.js";

run(process.argv.slice(2))
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    process.stderr.write(`[animica-node] fatal: ${err?.stack ?? err}\n`);
    process.exit(2);
  });
