// build.rs
// Animica Native — build script
//
// Responsibilities:
// • Detect target CPU features (x86_64: AVX2/AES/SHA; aarch64: NEON/SHA2/SHA3/AES)
//   and expose easy-to-use cfgs:
//       has_avx2, has_aesni, has_x86_sha,
//       has_neon, has_arm_sha2, has_arm_sha3, has_arm_aes,
//       simd_optimized
// • Optional C builds via the `cc` crate when the `c_keccak` feature is enabled.
// • Optional ISA-L discovery via pkg-config when the `isal` feature is enabled.
//
// Safe defaults: if features aren’t present, no cfg is emitted and portable
// code paths are used instead.

use std::collections::HashSet;
use std::env;
use std::path::PathBuf;

fn target_features() -> (String, HashSet<String>) {
    let arch = env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
    let feats_raw = env::var("CARGO_CFG_TARGET_FEATURE").unwrap_or_default();
    let mut feats = HashSet::new();
    for f in feats_raw.split(',') {
        let f = f.trim();
        if !f.is_empty() {
            feats.insert(f.to_string());
        }
    }
    (arch, feats)
}

fn emit_cpu_cfgs() {
    let (arch, feats) = target_features();

    if arch == "x86_64" {
        if feats.contains("avx2") {
            println!("cargo:rustc-cfg=has_avx2");
        }
        if feats.contains("aes") {
            println!("cargo:rustc-cfg=has_aesni");
        }
        if feats.contains("sha") {
            println!("cargo:rustc-cfg=has_x86_sha");
        }
        if feats.contains("avx2") || feats.contains("sse4.2") {
            println!("cargo:rustc-cfg=simd_optimized");
        }
    } else if arch == "aarch64" {
        if feats.contains("neon") {
            println!("cargo:rustc-cfg=has_neon");
        }
        if feats.contains("sha2") {
            println!("cargo:rustc-cfg=has_arm_sha2");
        }
        if feats.contains("sha3") {
            println!("cargo:rustc-cfg=has_arm_sha3");
        }
        if feats.contains("aes") {
            println!("cargo:rustc-cfg=has_arm_aes");
        }
        if feats.contains("neon") || feats.contains("sha2") || feats.contains("sha3") {
            println!("cargo:rustc-cfg=simd_optimized");
        }
    }
}

fn maybe_build_c_keccak() {
    // Only build the C Keccak backend if the crate feature is enabled.
    if env::var_os("CARGO_FEATURE_C_KECCAK").is_none() {
        return;
    }

    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let c_dir = manifest_dir.join("c").join("keccak");
    let c_file = c_dir.join("keccak1600.c");
    let header = c_dir.join("keccak1600.h");

    println!("cargo:rerun-if-changed={}", c_file.display());
    println!("cargo:rerun-if-changed={}", header.display());

    let mut build = cc::Build::new();
    build
        .file(&c_file)
        .include(&c_dir)
        .flag_if_supported("-std=c99");

    build.compile("keccak1600");
}

fn maybe_probe_isal() {
    // Only do ISA-L discovery if the Cargo feature is enabled.
    if env::var_os("CARGO_FEATURE_ISAL").is_none() {
        return;
    }

    // We depend on pkg-config via [build-dependencies].
    // Library::version is a String in modern pkg-config, so we do not pattern
    // match on it as an Option to avoid API mismatch.
    match pkg_config::Config::new().probe("isal") {
        Ok(lib) => {
            // Emit a cfg so Rust code can gate ISA-L accelerated paths.
            println!("cargo:rustc-cfg=has_isal");

            // Emit some helpful diagnostics while building.
            println!(
                "cargo:warning=Found ISA-L via pkg-config: libs={:?}, link_paths={:?}, version={}",
                lib.libs, lib.link_paths, lib.version
            );
        }
        Err(err) => {
            println!(
                "cargo:warning=pkg-config probe for ISA-L failed (falling back to pure-Rust paths): {err}"
            );
        }
    }
}

fn main() {
    // Re-run if environment toggles SIMD/feature detection or files change.
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=CARGO_CFG_TARGET_ARCH");
    println!("cargo:rerun-if-env-changed=CARGO_CFG_TARGET_FEATURE");
    println!("cargo:rerun-if-env-changed=CARGO_FEATURE_C_KECCAK");
    println!("cargo:rerun-if-env-changed=CARGO_FEATURE_ISAL");
    println!("cargo:rerun-if-env-changed=ANIMICA_NATIVE_FORCE_CPU");

    emit_cpu_cfgs();
    maybe_build_c_keccak();
    maybe_probe_isal();
}
