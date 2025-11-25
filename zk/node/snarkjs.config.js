/**
 * Animica — snarkjs build configuration (ESM)
 *
 * This file centralizes paths and options used by our helper scripts to build
 * and re-build Circom circuits with snarkjs in a deterministic, reproducible
 * way across machines and CI.
 *
 * Why a config file?
 * - The snarkjs CLI itself does not read configs, but our wrapper scripts
 *   import this module to locate circuits, ptau files, and to apply consistent
 *   compiler/CLI flags.
 * - Keeping all paths here avoids accidental divergences between local runs,
 *   CI, and docs.
 *
 * Reproducibility guidance:
 * - Lock Node & npm versions (see package.json "engines"/"packageManager").
 * - Lock snarkjs version (package.json devDependencies/overrides).
 * - Use the same ptau file content (verify blake3/sha256 checksums out-of-band).
 * - Set a deterministic RNG seed (we provide a default and an env override).
 * - Avoid auto-parallelism variance (we pin workerThreads=0 by default).
 */

const cfg = Object.freeze({
  /**
   * Where compiled artifacts and caches land.
   * Scripts will create these if they don't exist.
   */
  build: {
    // Root folder for compiled artifacts (r1cs/wasm/zkey/vkey/proofs, etc.)
    outRoot: "../artifacts",

    // Temporary working directory. Safe to delete; used by CLI tooling.
    tmpRoot: "../.cache",

    /**
     * Deterministic knobs:
     * - seed: default seed when environment is not providing one.
     * - envVar: allows external scripts/CI jobs to inject a fixed seed.
     * - workerThreads: using 0 disables worker pools to reduce ordering noise.
     */
    deterministic: {
      seed: "animica-reproducible-v1",
      envVar: "ANIMICA_CIRCOM_SEED",
      workerThreads: 0
    },

    /**
     * CLI entrypoints. We keep them explicit so wrapper scripts can call
     * without relying on shell resolution differences across OSes/CI.
     */
    cli: {
      circom: "circom",
      snarkjs: "node ./node_modules/.bin/snarkjs"
    },

    /**
     * Common Circom compile flags. We prefer O2 for small fixtures to keep
     * compile times pleasant while producing stable R1CS.
     *
     * Notes:
     * - For BN254 (alt-bn128), Circom expects `--prime bn128`.
     * - If debugging constraints, change to -O0 and keep --sym enabled.
     */
    circomArgs: ["--r1cs", "--wasm", "--sym", "-O2", "--prime", "bn128"]
  },

  /**
   * Powers of Tau locations. Keep small/medium variants around to match the
   * size of circuits under tests; larger ptau files increase ceremony time.
   *
   * You can host these in an internal cache and verify their checksums before
   * usage. Wrapper scripts can assert SHA3-256 and file size.
   */
  ptau: {
    // Small is enough for toy fixtures (depth 10).
    small: "../artifacts/powersOfTau28_hez_final_10.ptau",
    // Medium for slightly larger circuits, if you expand tests later.
    medium: "../artifacts/powersOfTau28_hez_final_15.ptau"
  },

  /**
   * Circuits registry:
   * Each entry describes a circuit with its source, protocol, ptau selection,
   * and canonical artifact paths used by build/verify scripts.
   */
  circuits: {
    // Groth16: embedding threshold check (poseidon inside circuit)
    embedding: {
      protocol: "groth16",
      name: "embedding",
      // Circom source
      src: "../circuits/groth16/embedding/embedding.circom",
      // Where circom will emit outputs
      outDir: "../circuits/groth16/embedding",
      // Which ptau to use for setup
      ptau: "small",
      // Expected artifact paths (used/verified by scripts)
      artifacts: {
        r1cs: "../circuits/groth16/embedding/embedding.r1cs",
        wasm: "../circuits/groth16/embedding/embedding_js/embedding.wasm",
        zkey: "../circuits/groth16/embedding/embedding_final.zkey",
        vkeyJson: "../circuits/groth16/embedding/vk.json",
        proofJson: "../circuits/groth16/embedding/proof.json",
        publicJson: "../circuits/groth16/embedding/public.json",
        sym: "../circuits/groth16/embedding/embedding.sym"
      },
      // snarkjs flags for Groth16 (phase 2)
      snarkjs: {
        // Phase 2 contributes randomness; we stabilize via deterministic seed.
        groth16: {
          // Optional: contribution label for ceremony logs
          contributionName: "animica-ci",
          // zkey rounds / entropy size are fixed by snarkjs; seed controls RNG.
        }
      }
    },

    // Groth16: storage PoRep stub (toy constraints)
    storage_porep_stub: {
      protocol: "groth16",
      name: "storage_porep_stub",
      src: "../circuits/groth16/storage_porep_stub/circuit.circom",
      outDir: "../circuits/groth16/storage_porep_stub",
      ptau: "small",
      artifacts: {
        r1cs: "../circuits/groth16/storage_porep_stub/circuit.r1cs",
        wasm: "../circuits/groth16/storage_porep_stub/circuit_js/circuit.wasm",
        zkey: "../circuits/groth16/storage_porep_stub/circuit_final.zkey",
        vkeyJson: "../circuits/groth16/storage_porep_stub/vk.json",
        proofJson: "../circuits/groth16/storage_porep_stub/proof.json",
        publicJson: "../circuits/groth16/storage_porep_stub/public.json",
        sym: "../circuits/groth16/storage_porep_stub/circuit.sym"
      }
    },

    // PLONK (KZG): Poseidon hash check
    poseidon_hash: {
      protocol: "plonk-kzg",
      name: "poseidon_hash",
      src: "../circuits/plonk_kzg/poseidon_hash/circuit.circom",
      outDir: "../circuits/plonk_kzg/poseidon_hash",
      // plonk+kzg in snarkjs still consumes a ptau (universal) file:
      ptau: "small",
      artifacts: {
        r1cs: "../circuits/plonk_kzg/poseidon_hash/circuit.r1cs",
        wasm: "../circuits/plonk_kzg/poseidon_hash/circuit_js/circuit.wasm",
        zkey: "../circuits/plonk_kzg/poseidon_hash/circuit_final.zkey",
        vkeyJson: "../circuits/plonk_kzg/poseidon_hash/vk.json",
        proofJson: "../circuits/plonk_kzg/poseidon_hash/proof.json",
        publicJson: "../circuits/plonk_kzg/poseidon_hash/public.json",
        sym: "../circuits/plonk_kzg/poseidon_hash/circuit.sym"
      },
      snarkjs: {
        plonk: {
          // No additional flags; seed + fixed ptau ensure reproducibility.
        }
      }
    }
  }
});

/**
 * Helpers exposed for scripts:
 * - getSeed(): read env override or default.
 * - getPtauPath(key): resolve ptau alias to absolute/relative path.
 * - getCircuit(name): fetch circuit entry or throw.
 */
export function getSeed() {
  const v = process.env[cfg.build.deterministic.envVar];
  return (v && v.length > 0) ? v : cfg.build.deterministic.seed;
}

export function getPtauPath(key) {
  const p = cfg.ptau[key];
  if (!p) throw new Error(`Unknown ptau key: ${key}`);
  return p;
}

export function getCircuit(name) {
  const c = cfg.circuits[name];
  if (!c) throw new Error(`Unknown circuit: ${name}`);
  return c;
}

export default cfg;
