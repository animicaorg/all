#!/usr/bin/env node
/**
 * Animica — Groth16 embedding circuit: compile + setup + prove (developer helper)
 *
 * This script compiles the Circom circuit, runs Groth16 setup with a deterministic
 * contribution, generates a witness using the example input, produces a proof,
 * exports the verification key, and verifies the proof — all in one go.
 *
 * Prereqs (pinned in zk/node/package.json):
 *   - node >= 18
 *   - snarkjs (devDependency)
 *   - circom in PATH (recommended: circom 2.1.x)
 *
 * Files it (re)generates under zk/circuits/groth16/embedding/:
 *   - embedding.r1cs
 *   - embedding_js/embedding.wasm
 *   - embedding.sym
 *   - embedding_0000.zkey (intermediate)
 *   - embedding_final.zkey
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
const CIRCOM_BIN = cfg.build.cli.circom; // expects 'circom' in PATH
const SNARKJS_BIN = process.platform === "win32"
  ? path.join(NODE_DIR, "node_modules", ".bin", "snarkjs.cmd")
  : path.join(NODE_DIR, "node_modules", ".bin", "snarkjs");

function log(msg) {
  process.stdout.write(`[compile_embedding] ${msg}\n`);
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
  // Config
  const c = getCircuit("embedding");
  const seed = getSeed();
  if (!process.env[cfg.build.deterministic?.envVar ?? "ANIMICA_CIRCOM_SEED"]) {
    // Make the seed visible to sub-processes and logs (snarkjs contributes via -e)
    process.env.ANIMICA_CIRCOM_SEED = seed;
  }

  // Resolve all important paths relative to zk/node/
  const src = path.resolve(NODE_DIR, c.src);
  const outDir = path.resolve(NODE_DIR, c.outDir);
  const r1cs = path.resolve(NODE_DIR, c.artifacts.r1cs);
  const wasm = path.resolve(NODE_DIR, c.artifacts.wasm);
  const sym = path.resolve(NODE_DIR, c.artifacts.sym);
  const zkeyFinal = path.resolve(NODE_DIR, c.artifacts.zkey);
  const zkey0 = zkeyFinal.replace(/_final\.zkey$/, "_0000.zkey");
  const vkeyJson = path.resolve(NODE_DIR, c.artifacts.vkeyJson);
  const proofJson = path.resolve(NODE_DIR, c.artifacts.proofJson);
  const publicJson = path.resolve(NODE_DIR, c.artifacts.publicJson);
  const witness = path.join(path.dirname(wasm), "..", "witness.wtns"); // alongside wasm folder
  const inputExample = path.resolve(outDir, "input_example.json"); // lives in circuit folder

  const ptau = path.resolve(NODE_DIR, getPtauPath(c.ptau));

  log("=== Configuration ===");
  log(`Circuit:        ${c.name} (${c.protocol})`);
  log(`Circom source:  ${src}`);
  log(`Out dir:        ${outDir}`);
  log(`PTAU:           ${ptau}`);
  log(`Seed:           ${seed}`);
  log("=====================");

  // Sanity: snarkjs installed locally?
  if (!(await pathExists(SNARKJS_BIN))) {
    throw new Error(
      `snarkjs binary not found at ${SNARKJS_BIN}.
Ensure you ran 'npm ci' in zk/node.`
    );
  }

  // Sanity: ptau exists
  if (!(await pathExists(ptau))) {
    throw new Error(
      `PTAU file missing:\n  ${ptau}\n\n` +
      `Hints:\n` +
      `  - Place the expected ptau at that path (see zk/node/snarkjs.config.js).\n` +
      `  - Or run: python zk/scripts/fetch_circom_artifacts.py --ptau small\n` +
      `  - Or generate your own Powers of Tau (snarkjs powersoftau ...), then copy here.`
    );
  }

  // Sanity: example input exists (used to generate witness/proof)
  if (!(await pathExists(inputExample))) {
    throw new Error(
      `Example input not found at:\n  ${inputExample}\n` +
      `Create it or copy from zk/circuits/groth16/embedding/input_example.json template.`
    );
  }

  // Ensure directories exist
  await ensureDir(outDir);
  await ensureDir(path.dirname(wasm)); // embedding_js/

  // 1) Compile circom -> r1cs/wasm/sym
  log("Step 1/6 — circom compile");
  const circomArgs = [
    src,
    ...cfg.build.circomArgs,
    "-o",
    outDir
  ];
  await run(CIRCOM_BIN, circomArgs, {
    env: {
      // Keep worker count stable for determinism (circom uses threads for codegen)
      CIRCOM_WORKER_THREADS: String(cfg.build.deterministic.workerThreads ?? 0),
    },
  });

  // 2) Groth16 setup -> zkey0 (deterministic given ptau + r1cs)
  log("Step 2/6 — groth16 setup");
  await run(SNARKJS_BIN, ["groth16", "setup", r1cs, ptau, zkey0]);

  // 3) Contribute fixed entropy -> final zkey
  log("Step 3/6 — zkey contribute (deterministic)");
  const contribName = (c.snarkjs?.groth16?.contributionName) || "animica-ci";
  await run(SNARKJS_BIN, [
    "zkey",
    "contribute",
    zkey0,
    zkeyFinal,
    "--name",
    contribName,
    "-e",
    seed,
  ]);

  // 4) Export verification key
  log("Step 4/6 — export verification key");
  await run(SNARKJS_BIN, ["zkey", "export", "verificationkey", zkeyFinal, vkeyJson]);

  // 5) Generate witness from example input
  log("Step 5/6 — witness generation");
  await run(SNARKJS_BIN, ["wtns", "calculate", wasm, inputExample, witness]);

  // 6) Prove + write proof/public
  log("Step 6/6 — proving");
  await run(SNARKJS_BIN, ["groth16", "prove", zkeyFinal, witness, proofJson, publicJson]);

  // Verify proof for good measure
  log("Verification — groth16 verify");
  await run(SNARKJS_BIN, ["groth16", "verify", vkeyJson, publicJson, proofJson]);

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
