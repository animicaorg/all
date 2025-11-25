#!/usr/bin/env node
/**
 * Animica — PLONK(KZG) Poseidon Hash circuit: compile + setup + prove
 *
 * One-shot helper that:
 *   1) Compiles Circom → R1CS/WASM/SYM
 *   2) Runs PLONK(KZG) setup using a PTAU file
 *   3) Exports the verification key (vk.json)
 *   4) Generates a witness from example inputs
 *   5) Produces a proof + public signals
 *   6) Verifies the proof
 *
 * Prereqs (pinned in zk/node/package.json):
 *   - Node >= 18
 *   - snarkjs installed locally (npm ci in zk/node)
 *   - circom in PATH (recommended: circom 2.1.x)
 *   - a Powers of Tau file (PTAU), see zk/node/snarkjs.config.js
 *
 * Outputs under zk/circuits/plonk_kzg/poseidon_hash/:
 *   - poseidon_hash.r1cs
 *   - poseidon_hash_js/poseidon_hash.wasm
 *   - poseidon_hash.sym
 *   - poseidon_hash_final.zkey
 *   - vk.json
 *   - witness.wtns
 *   - public.json
 *   - proof.json
 */

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

import cfg, { getCircuit, getPtauPath, getSeed } from "../snarkjs.config.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const NODE_DIR = path.resolve(__dirname, ".."); // zk/node/
const CWD = NODE_DIR;

// Resolve executables
const CIRCOM_BIN = cfg.build.cli.circom; // e.g. "circom"
const SNARKJS_BIN = process.platform === "win32"
  ? path.join(NODE_DIR, "node_modules", ".bin", "snarkjs.cmd")
  : path.join(NODE_DIR, "node_modules", ".bin", "snarkjs");

function log(msg) {
  process.stdout.write(`[compile_poseidon] ${msg}\n`);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    log(`$ ${cmd} ${args.join(" ")}`);
    const child = spawn(cmd, args, {
      cwd: CWD,
      stdio: "inherit",
      env: { ...process.env, ...opts.env },
      shell: false,
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

async function ensureDir(p) {
  await fs.mkdir(p, { recursive: true });
}

async function pathExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  // Pull circuit descriptor from central config
  // Expect a circuit entry named "poseidon_hash" under protocol "plonk_kzg"
  const c = getCircuit("poseidon_hash");
  const seed = getSeed(); // for reproducible logs; PLONK has no contribution step, but we log it

  // Resolve important paths relative to zk/node/
  const src = path.resolve(NODE_DIR, c.src);
  const outDir = path.resolve(NODE_DIR, c.outDir);
  const r1cs = path.resolve(NODE_DIR, c.artifacts.r1cs);
  const wasm = path.resolve(NODE_DIR, c.artifacts.wasm);
  const sym = path.resolve(NODE_DIR, c.artifacts.sym);
  const zkeyFinal = path.resolve(NODE_DIR, c.artifacts.zkey);
  const vkeyJson = path.resolve(NODE_DIR, c.artifacts.vkeyJson);
  const proofJson = path.resolve(NODE_DIR, c.artifacts.proofJson);
  const publicJson = path.resolve(NODE_DIR, c.artifacts.publicJson);
  const witness = path.join(path.dirname(wasm), "..", "witness.wtns");
  const inputExample = path.resolve(outDir, "public.json"); // same folder as circuit artifacts

  const ptau = path.resolve(NODE_DIR, getPtauPath(c.ptau));

  log("=== Configuration ===");
  log(`Circuit:        ${c.name} (${c.protocol})`);
  log(`Circom source:  ${src}`);
  log(`Out dir:        ${outDir}`);
  log(`PTAU:           ${ptau}`);
  log(`Seed (log):     ${seed}`);
  log("=====================");

  // Sanity checks
  if (!(await pathExists(SNARKJS_BIN))) {
    throw new Error(
      `snarkjs binary not found at ${SNARKJS_BIN}.
Run:  (cd zk/node && npm ci)`
    );
  }
  if (!(await pathExists(ptau))) {
    throw new Error(
      `PTAU file missing:\n  ${ptau}\n\nHints:\n` +
      `  - Check zk/node/snarkjs.config.js for expected location/name\n` +
      `  - Try: python zk/scripts/fetch_circom_artifacts.py --ptau small\n` +
      `  - Or generate your own Powers of Tau and put it there`
    );
  }
  if (!(await pathExists(src))) {
    throw new Error(`Circom source not found at:\n  ${src}`);
  }
  // Example PLONK input (public.json) used for witness and public signals checks
  if (!(await pathExists(inputExample))) {
    throw new Error(
      `Example input (public.json) not found at:\n  ${inputExample}\n` +
      `Provide it (see zk/circuits/plonk_kzg/poseidon_hash/public.json)`
    );
  }

  // Ensure output dirs exist
  await ensureDir(outDir);
  await ensureDir(path.dirname(wasm)); // poseidon_hash_js/

  // 1) Compile circom -> r1cs/wasm/sym
  //    NOTE: For PLONK, we still compile to R1CS; snarkjs plonk setup consumes R1CS.
  log("Step 1/6 — circom compile");
  const circomArgs = [
    src,
    ...cfg.build.circomArgs,
    "-o",
    outDir,
  ];
  await run(CIRCOM_BIN, circomArgs, {
    env: {
      // Reduce nondeterminism from multi-threaded codegen in some circom builds
      CIRCOM_WORKER_THREADS: String(cfg.build.deterministic.workerThreads ?? 0),
    },
  });

  // 2) PLONK(KZG) setup -> final zkey (no contribution loop)
  log("Step 2/6 — plonk setup");
  await run(SNARKJS_BIN, ["plonk", "setup", r1cs, ptau, zkeyFinal]);

  // 3) Export verification key
  log("Step 3/6 — export verification key");
  await run(SNARKJS_BIN, ["zkey", "export", "verificationkey", zkeyFinal, vkeyJson]);

  // 4) Generate witness (inputExample is already the canonical public.json for this toy circuit)
  //    Some Poseidon circuits use only public inputs; still, witness is required for proving.
  log("Step 4/6 — witness generation");
  await run(SNARKJS_BIN, ["wtns", "calculate", wasm, inputExample, witness]);

  // 5) Prove with PLONK
  log("Step 5/6 — proving (plonk)");
  await run(SNARKJS_BIN, ["plonk", "prove", zkeyFinal, witness, proofJson, publicJson]);

  // 6) Verify proof
  log("Step 6/6 — verify");
  await run(SNARKJS_BIN, ["plonk", "verify", vkeyJson, publicJson, proofJson]);

  // Summary
  log("=== DONE ===");
  log(`R1CS:           ${path.relative(CWD, r1cs)}`);
  log(`WASM:           ${path.relative(CWD, wasm)}`);
  log(`ZKey (final):   ${path.relative(CWD, zkeyFinal)}`);
  log(`VK JSON:        ${path.relative(CWD, vkeyJson)}`);
  log(`Witness:        ${path.relative(CWD, witness)}`);
  log(`Public JSON:    ${path.relative(CWD, publicJson)}`);
  log(`Proof JSON:     ${path.relative(CWD, proofJson)}`);
  log("Proof verified successfully ✅");
}

main().catch((err) => {
  console.error("\nFAILED:", err?.message ?? err);
  process.exit(1);
});
