// Copyright (c) Animica
// SPDX-License-Identifier: Apache-2.0
//
// build.rs — animica_zk_native
//
// Emits feature flags and environment metadata for the native zk accelerators.
// - Detects enabled Cargo features (pairing, kzg, python, parallel) and exposes
//   `cfg(build_has_*)` for conditional compilation in Rust code.
// - Exposes git describe + target triple via `ANIMICA_ZK_NATIVE_*` env vars.
// - Wires common env toggles as "rerun-if-env-changed" so `cargo` rebuilds
//   correctly when switching backends or Python config.
//
// This script is intentionally light-touch: pyo3 config is discovered by pyo3
// itself; we only surface useful metadata and cfg flags.

use std::env;
use std::process::Command;

fn git_describe() -> Option<String> {
    let out = Command::new("git")
        .args(["describe", "--tags", "--dirty", "--always"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_owned();
    if s.is_empty() { None } else { Some(s) }
}

fn main() {
    // Re-run policy
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=ZK_DISABLE_NATIVE");
    println!("cargo:rerun-if-env-changed=ZK_FORCE_PYECC");
    println!("cargo:rerun-if-env-changed=PYTHON_SYS_EXECUTABLE");
    println!("cargo:rerun-if-env-changed=PYO3_CONFIG_FILE");
    println!("cargo:rerun-if-env-changed=MATURIN_PYTHON_SYSCONFIGDATA_DIR");

    // Target/profile metadata
    let target = env::var("TARGET").unwrap_or_default();
    let profile = env::var("PROFILE").unwrap_or_default();
    println!("cargo:rustc-env=ANIMICA_ZK_NATIVE_TARGET={}", target);
    println!("cargo:rustc-env=ANIMICA_ZK_NATIVE_PROFILE={}", profile);

    if let Some(desc) = git_describe() {
        println!("cargo:rustc-env=ANIMICA_ZK_NATIVE_GIT={}", desc);
    }

    // Detect enabled features from Cargo
    let feat_pairing = env::var("CARGO_FEATURE_PAIRING").is_ok();
    let feat_kzg = env::var("CARGO_FEATURE_KZG").is_ok();
    let feat_python = env::var("CARGO_FEATURE_PYTHON").is_ok();
    let feat_parallel = env::var("CARGO_FEATURE_PARALLEL").is_ok();

    if feat_pairing {
        println!("cargo:rustc-cfg=build_has_pairing");
    }
    if feat_kzg {
        println!("cargo:rustc-cfg=build_has_kzg");
    }
    if feat_python {
        println!("cargo:rustc-cfg=build_has_python");
    }
    if feat_parallel {
        println!("cargo:rustc-cfg=build_has_parallel");
    }

    // Surface backend toggles for code to inspect at compile-time (optional).
    let disable_native = env::var("ZK_DISABLE_NATIVE").unwrap_or_else(|_| "0".into());
    let force_pyecc = env::var("ZK_FORCE_PYECC").unwrap_or_else(|_| "0".into());
    println!("cargo:rustc-env=ANIMICA_ZK_DISABLE_NATIVE={}", disable_native);
    println!("cargo:rustc-env=ANIMICA_ZK_FORCE_PYECC={}", force_pyecc);

    // Helpful one-line summary in build logs.
    println!(
        "cargo:warning=animica_zk_native: target={} profile={} features: pairing={} kzg={} python={} parallel={} (ZK_DISABLE_NATIVE={}, ZK_FORCE_PYECC={})",
        target, profile, feat_pairing, feat_kzg, feat_python, feat_parallel, disable_native, force_pyecc
    );

    // Platform hints (no-op but visible when debugging builds)
    #[cfg(target_os = "windows")]
    println!("cargo:warning=animica_zk_native: building on Windows; ensure MSVC toolchain is set for pyo3 wheels if feature=python.");
    #[cfg(target_os = "macos")]
    println!("cargo:warning=animica_zk_native: building on macOS; universal builds may require explicit targets (aarch64-apple-darwin / x86_64-apple-darwin).");
    #[cfg(target_os = "linux")]
    println!("cargo:warning=animica_zk_native: building on Linux; manylinux/musllinux wheels require maturin if feature=python.");
}
