#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministically (re)generate zk fixture vectors (vk.json, proof.json, public.json).

Supported topologies (auto-discovered):
  zk/circuits/groth16/*/*.circom
  zk/circuits/plonk_kzg/*/*.circom
STARK demos are out-of-scope for this script.

Prereqs:
  - Node.js in PATH
  - A resolvable 'snarkjs' module for Node (`npm i -g snarkjs` or project-local)
  - Circuits compiled & zkeys produced (see zk/scripts/build_circom.sh)

Determinism strategy:
  - VK: exported from the zkey (pure function) ⇒ deterministic.
  - Groth16 prove: patch Node's crypto RNG with a seeded xorshift128+ PRNG.
    Seed = sha256(circuitPath || wasmBytes || zkeyBytes || inputJsonBytes).
  - PLONK-KZG prove: deterministic via fiat-shamir transcript; no RNG override required,
    but we still provide the same seeding path for uniformity.

Outputs:
  - <root>/<system>/<name>/vk.json
  - <root>/<system>/<name>/proof.json (if input_example.json present)
  - <root>/<system>/<name>/public.json (if input_example.json present)

Usage:
  python3 zk/scripts/generate_vectors.py [--root zk/circuits] [--only SUBSTR]
                                        [--ensure-built] [--rewrite|--check]
                                        [--no-proof] [--no-vk]

Flags:
  --only SUBSTR      Restrict to circuits where "system/name" contains SUBSTR.
  --ensure-built     If required artifacts (.wasm/.zkey) are missing, call
                    zk/scripts/build_circom.sh to produce them.
  --rewrite          Always rewrite vk/proof/public (even if unchanged).
  --check            Do not write; fail (exit 2) if any output would change.
  --no-proof         Skip proof/public generation (refresh VKs only).
  --no-vk            Skip VK export (useful if you only want proofs).
  -v / --verbose     More logging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -------------------------------
# Configuration
# -------------------------------
DEFAULT_ROOT = Path("zk/circuits")
BUILD_SCRIPT = Path("zk/scripts/build_circom.sh")


# -------------------------------
# Small utilities
# -------------------------------
def log(msg: str, *, verbose: bool = True) -> None:
    if verbose:
        print(msg)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(p: Path) -> bytes:
    with p.open("rb") as f:
        return f.read()


def read_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_canonical(
    p: Path, obj, *, dry_run: bool, rewrite: bool, verbose: bool
) -> Tuple[bool, str]:
    """
    Write compact, sorted-key JSON with trailing newline. Returns (changed, new_hash_hex).
    Honors dry-run and rewrite flags. If file exists and content identical, no rewrite.
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
    new_hash = sha256_hex(payload.encode("utf-8"))

    if p.exists():
        old = p.read_text(encoding="utf-8")
        if old == payload:
            log(f"    = up-to-date {p.name} (sha256:{new_hash[:10]}…)", verbose=verbose)
            return (False, new_hash)
        if dry_run and not rewrite:
            log(
                f"    ~ would update {p.name} (sha256:{new_hash[:10]}…)",
                verbose=verbose,
            )
            return (True, new_hash)
    if dry_run and not rewrite:
        log(f"    + would write {p.name} (sha256:{new_hash[:10]}…)", verbose=verbose)
        return (True, new_hash)
    p.write_text(payload, encoding="utf-8")
    log(f"    ✓ wrote {p.name} (sha256:{new_hash[:10]}…)", verbose=verbose)
    return (True, new_hash)


def ensure_exe(cmd: str) -> None:
    from shutil import which

    if which(cmd) is None:
        sys.exit(
            f"✖ Missing executable: {cmd}. Please install it and ensure it's in PATH."
        )


# -------------------------------
# Discovery
# -------------------------------
@dataclass
class Circuit:
    system: str  # "groth16" | "plonk_kzg"
    name: str  # directory name under system/
    circom: Path  # path to .circom
    outdir: Path  # system/name
    builddir: Path  # system/name/build
    base: str  # basename of .circom (e.g., "embedding", "circuit")
    wasm: Path  # build/<base>.wasm
    zkey: Path  # build/<base>.zkey
    input_example: Path  # outdir/input_example.json (optional)
    vk_json: Path  # outdir/vk.json
    proof_json: Path  # outdir/proof.json
    public_json: Path  # outdir/public.json


def discover_circuits(root: Path, only: Optional[str]) -> List[Circuit]:
    circuits: List[Circuit] = []
    for system in ("groth16", "plonk_kzg"):
        sys_dir = root / system
        if not sys_dir.exists():
            continue
        for sub in sys_dir.iterdir():
            if not sub.is_dir():
                continue
            cirs = list(sub.glob("*.circom"))
            if not cirs:
                continue
            circom = cirs[0]  # expect a single .circom per circuit directory
            base = circom.stem
            outdir = sub
            builddir = outdir / "build"
            wasm = builddir / f"{base}.wasm"
            zkey = builddir / f"{base}.zkey"
            c = Circuit(
                system=system,
                name=sub.name,
                circom=circom,
                outdir=outdir,
                builddir=builddir,
                base=base,
                wasm=wasm,
                zkey=zkey,
                input_example=outdir / "input_example.json",
                vk_json=outdir / "vk.json",
                proof_json=outdir / "proof.json",
                public_json=outdir / "public.json",
            )
            if only and f"{system}/{c.name}".find(only) < 0:
                continue
            circuits.append(c)
    return circuits


# -------------------------------
# Node driver (deterministic RNG + snarkjs)
# -------------------------------
JS_DRIVER = r"""#!/usr/bin/env node
/* Deterministic snarkjs driver.
   - mode: "vk"    => export verification key from .zkey
   - mode: "prove" => fullProve (groth16|plonk_kzg) with optional deterministic RNG
   - mode: "verify"=> verify (groth16|plonk_kzg)
   Args: JSON in argv[2] with fields:
     { mode, system, wasm, zkey, input, out, proof_out, public_out, vk, seedHex }
*/
const fs = require('fs');

function makePRNG(seedHex) {
  // xorshift128+ over 64-bit state using BigInt
  // Seed expansion: derive two 64-bit non-zero states from seedHex (sha256 hex).
  let s1 = 0x0123456789abcdn;
  let s2 = 0xfedcba987654321n;
  if (seedHex && seedHex.length >= 32) {
    const a = BigInt('0x' + seedHex.slice(0, 16));
    const b = BigInt('0x' + seedHex.slice(16, 32));
    const c = BigInt('0x' + seedHex.slice(32, 48));
    const d = BigInt('0x' + seedHex.slice(48, 64));
    s1 = (a ^ (c << 1n)) & ((1n << 64n) - 1n);
    s2 = (b ^ (d << 1n)) & ((1n << 64n) - 1n);
    if (s1 === 0n && s2 === 0n) s2 = 1n;
  }
  function next64() {
    let x = s1, y = s2;
    s1 = y;
    x ^= (x << 23n) & ((1n << 64n) - 1n);
    x ^= (x >> 17n);
    x ^= y ^ (y >> 26n);
    s2 = x;
    return (x + y) & ((1n << 64n) - 1n);
  }
  function fill(buf, offset = 0, size = buf.length - offset) {
    let i = offset;
    const end = offset + size;
    while (i < end) {
      const v = next64();
      for (let j = 0; j < 8 && i < end; j++) {
        buf[i++] = Number((v >> BigInt(8 * j)) & 0xffn);
      }
    }
    return buf;
  }
  return { next64, fill };
}

function patchCrypto(seedHex) {
  const crypto = require('crypto');
  const prng = makePRNG(seedHex);
  crypto.randomBytes = function (n) {
    const b = Buffer.alloc(n);
    prng.fill(b);
    return b;
  };
  crypto.randomFillSync = function (buf, offset = 0, size) {
    prng.fill(buf, offset, size ?? (buf.length - offset));
    return buf;
  };
}

async function main() {
  if (process.argv.length < 3) {
    console.error("usage: driver <json-args>");
    process.exit(2);
  }
  const cfg = JSON.parse(process.argv[2]);

  // Patch RNG for Groth16 proving if a seed is provided
  if (cfg.seedHex && cfg.mode === "prove" && cfg.system === "groth16") {
    patchCrypto(cfg.seedHex);
  }

  const snarkjs = require('snarkjs');

  if (cfg.mode === "vk") {
    const zkeyBytes = new Uint8Array(fs.readFileSync(cfg.zkey));
    const vk = await snarkjs.zKey.exportVerificationKey(zkeyBytes);
    fs.writeFileSync(cfg.out, JSON.stringify(vk, null, 2));
    return;
  }

  if (cfg.mode === "prove") {
    const input = JSON.parse(fs.readFileSync(cfg.input, 'utf8'));
    if (cfg.system === "groth16") {
      const { proof, publicSignals } = await snarkjs.groth16.fullProve(input, cfg.wasm, cfg.zkey);
      fs.writeFileSync(cfg.proof_out, JSON.stringify(proof, null, 2));
      fs.writeFileSync(cfg.public_out, JSON.stringify(publicSignals, null, 2));
      return;
    } else if (cfg.system === "plonk_kzg") {
      const { proof, publicSignals } = await snarkjs.plonk.fullProve(input, cfg.wasm, cfg.zkey);
      fs.writeFileSync(cfg.proof_out, JSON.stringify(proof, null, 2));
      fs.writeFileSync(cfg.public_out, JSON.stringify(publicSignals, null, 2));
      return;
    } else {
      throw new Error("Unsupported system for prove: " + cfg.system);
    }
  }

  if (cfg.mode === "verify") {
    const proof = JSON.parse(fs.readFileSync(cfg.proof, 'utf8'));
    const publicSignals = JSON.parse(fs.readFileSync(cfg.public, 'utf8'));
    const vk = JSON.parse(fs.readFileSync(cfg.vk, 'utf8'));
    let ok = false;
    if (cfg.system === "groth16") {
      ok = await snarkjs.groth16.verify(vk, publicSignals, proof);
    } else if (cfg.system === "plonk_kzg") {
      ok = await snarkjs.plonk.verify(vk, publicSignals, proof);
    } else {
      throw new Error("Unsupported system for verify: " + cfg.system);
    }
    if (!ok) {
      console.error("verification failed");
      process.exit(3);
    }
    return;
  }

  throw new Error("Unknown mode: " + cfg.mode);
}

main().catch(e => {
  console.error(e);
  process.exit(1);
});
"""


def ensure_js_driver(tmpdir: Path) -> Path:
    driver = tmpdir / "snarkjs_driver.js"
    driver.write_text(JS_DRIVER, encoding="utf-8")
    return driver


def node_exec(driver: Path, args_obj: Dict, *, verbose: bool) -> None:
    args_json = json.dumps(args_obj, separators=(",", ":"))
    cmd = ["node", str(driver), args_json]
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        raise SystemExit(proc.returncode)
    if proc.stdout and verbose:
        sys.stdout.write(proc.stdout)


# -------------------------------
# Core workflow
# -------------------------------
def compute_seed_hex(c: Circuit) -> str:
    """
    Derive a stable seed from circuit path + wasm bytes + zkey bytes + input bytes (if present).
    This stabilizes Groth16 proving randomness across runs and machines.
    """
    h = hashlib.sha256()
    h.update(str(c.circom).encode())
    if c.wasm.exists():
        h.update(read_bytes(c.wasm))
    if c.zkey.exists():
        h.update(read_bytes(c.zkey))
    if c.input_example.exists():
        h.update(
            json.dumps(
                read_json(c.input_example), sort_keys=True, separators=(",", ":")
            ).encode()
        )
    return h.hexdigest()


def ensure_built_if_needed(c: Circuit, ensure_built: bool, verbose: bool) -> None:
    missing = []
    if not c.wasm.exists():
        missing.append(c.wasm)
    if not c.zkey.exists():
        missing.append(c.zkey)
    if not missing:
        return
    if not ensure_built:
        sys.exit(
            f"✖ Missing artifacts for {c.system}/{c.name}: {', '.join(map(str, missing))}\n"
            f"  Hint: run: bash {BUILD_SCRIPT} --only {c.system}/{c.name}"
        )
    # Try building
    ensure_exe("bash")
    cmd = ["bash", str(BUILD_SCRIPT), "--only", f"{c.system}/{c.name}"]
    log(f"  • building artifacts via: {' '.join(cmd)}", verbose=verbose)
    subprocess.check_call(cmd)
    # Re-check
    if not c.wasm.exists() or not c.zkey.exists():
        sys.exit(f"✖ Build did not produce required artifacts for {c.system}/{c.name}.")


def export_vk(
    c: Circuit, driver: Path, *, dry_run: bool, rewrite: bool, verbose: bool
) -> Tuple[bool, str]:
    # Run driver to get vk.json as pretty JSON, then canonicalize locally
    with tempfile.TemporaryDirectory() as td:
        tmp_vk = Path(td) / "vk.json"
        node_exec(
            driver,
            {
                "mode": "vk",
                "zkey": str(c.zkey),
                "out": str(tmp_vk),
            },
            verbose=verbose,
        )
        vk_obj = read_json(tmp_vk)
        return write_json_canonical(
            c.vk_json, vk_obj, dry_run=dry_run, rewrite=rewrite, verbose=verbose
        )


def prove_and_write(
    c: Circuit, driver: Path, *, dry_run: bool, rewrite: bool, verbose: bool
) -> Tuple[bool, bool]:
    """
    Generate proof.json + public.json deterministically (where possible),
    then verify, then write canonical JSON.
    Returns (changed_any, verified_ok).
    """
    if not c.input_example.exists():
        log("    - no input_example.json: skip proving", verbose=verbose)
        return (False, True)

    seed_hex = compute_seed_hex(c)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        tmp_proof = td_path / "proof.json"
        tmp_public = td_path / "public.json"

        node_exec(
            driver,
            {
                "mode": "prove",
                "system": c.system,
                "wasm": str(c.wasm),
                "zkey": str(c.zkey),
                "input": str(c.input_example),
                "proof_out": str(tmp_proof),
                "public_out": str(tmp_public),
                "seedHex": seed_hex,
            },
            verbose=verbose,
        )

        # Verify via driver
        tmp_vk = td_path / "vk.json"
        # Ensure we use the on-disk vk (which we *also* refresh in this run)
        vk_obj = read_json(c.vk_json) if c.vk_json.exists() else {}
        if not vk_obj:
            # If VK wasn't present yet, export to temp and use that
            node_exec(
                driver,
                {
                    "mode": "vk",
                    "zkey": str(c.zkey),
                    "out": str(tmp_vk),
                },
                verbose=verbose,
            )
            vk_obj = read_json(tmp_vk)
        # Quick verify
        verify_args = {
            "mode": "verify",
            "system": c.system,
            "vk": str(c.vk_json if c.vk_json.exists() else tmp_vk),
            "proof": str(tmp_proof),
            "public": str(tmp_public),
        }
        node_exec(driver, verify_args, verbose=verbose)

        # Canonicalize & write
        proof_obj = read_json(tmp_proof)
        public_obj = read_json(tmp_public)
        ch1, _ = write_json_canonical(
            c.proof_json, proof_obj, dry_run=dry_run, rewrite=rewrite, verbose=verbose
        )
        ch2, _ = write_json_canonical(
            c.public_json, public_obj, dry_run=dry_run, rewrite=rewrite, verbose=verbose
        )
        return (ch1 or ch2, True)


def process_circuit(
    c: Circuit,
    *,
    driver: Path,
    ensure_built: bool,
    do_vk: bool,
    do_proof: bool,
    dry_run: bool,
    rewrite: bool,
    verbose: bool,
) -> Tuple[bool, bool]:
    log(f"→ {c.system}/{c.name}", verbose=verbose)
    ensure_built_if_needed(c, ensure_built=ensure_built, verbose=verbose)

    changed = False
    verified = True

    if do_vk:
        log("  • exporting verification key", verbose=verbose)
        ch_vk, _ = export_vk(
            c, driver, dry_run=dry_run, rewrite=rewrite, verbose=verbose
        )
        changed = changed or ch_vk

    if do_proof:
        log("  • generating proof/public (deterministic)", verbose=verbose)
        ch_pf, ok = prove_and_write(
            c, driver, dry_run=dry_run, rewrite=rewrite, verbose=verbose
        )
        changed = changed or ch_pf
        verified = verified and ok

    return changed, verified


# -------------------------------
# Main CLI
# -------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Deterministically regenerate zk vectors (vk/proof/public)."
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Circuits root directory (default: zk/circuits)",
    )
    ap.add_argument(
        "--only", type=str, default="", help='Substring filter over "system/name"'
    )
    ap.add_argument(
        "--ensure-built",
        action="store_true",
        help="Run build_circom.sh if artifacts are missing",
    )
    ap.add_argument(
        "--rewrite",
        action="store_true",
        help="Always rewrite outputs (even if unchanged)",
    )
    ap.add_argument(
        "--check", action="store_true", help="Exit non-zero if outputs would change"
    )
    ap.add_argument(
        "--no-proof",
        action="store_true",
        help="Skip proof/public generation (refresh VK only)",
    )
    ap.add_argument("--no-vk", action="store_true", help="Skip verification key export")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = ap.parse_args()

    ensure_exe("node")

    circuits = discover_circuits(args.root, args.only or None)
    if not circuits:
        sys.exit("✖ No circuits found. Adjust --root/--only or add circuits.")

    # Prepare Node driver
    with tempfile.TemporaryDirectory() as td:
        driver = ensure_js_driver(Path(td))

        any_changed = False
        all_verified = True

        for c in circuits:
            ch, ok = process_circuit(
                c,
                driver=driver,
                ensure_built=args.ensure_built,
                do_vk=(not args.no_vk),
                do_proof=(not args.no_proof),
                dry_run=args.check and not args.rewrite,
                rewrite=args.rewrite,
                verbose=True if args.verbose else True,  # default: chatty
            )
            any_changed = any_changed or ch
            all_verified = all_verified and ok

    # Summary + exit code
    if args.check and any_changed:
        print("✖ Differences detected (check mode).")
        sys.exit(2)

    if not all_verified:
        print("✖ Verification failed for one or more circuits.")
        sys.exit(3)

    print("✓ Done.")
    sys.exit(0)


if __name__ == "__main__":
    main()
