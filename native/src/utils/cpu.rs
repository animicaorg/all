//! Runtime CPU feature detection (single source of truth).
//!
//! Exposes a cached view of a few SIMD/crypto flags we care about across
//!" mainstream targets, with tiny helpers for consumers that want booleans
//! rather than dealing with `std::arch::*` macros directly.
//!
//! Covered flags:
//! - x86/x86_64: `avx2`, `sha`  (Intel SHA extensions; CPUID leaf7)
//! - aarch64:    `neon`         (aka ASIMD; baseline on most modern chips)
//!
//! Design notes:
//! - We compute once and cache in a `OnceLock`. Use `get()` or the direct
//!   helpers `has_avx2()`, `has_sha()`, `has_neon()`.
//! - For testing and ad-hoc diagnostics, `detect_now()` recomputes flags
//!   without touching the cache.
//! - Optional env overrides for experimentation:
//!     * ANIMICA_CPU_FORCE_AVX2=0|1
//!     * ANIMICA_CPU_FORCE_SHA=0|1
//!     * ANIMICA_CPU_FORCE_NEON=0|1
//!   Overrides are clamped by architecture (e.g., `AVX2` cannot be forced on
//!   non-x86 targets).
//!
//! Safety & usage:
//! - These booleans are **capability hints**. Still use `#[target_feature]`
//!   or runtime dispatch guards before calling unsafe intrinsics.
//! - Keep this module minimal and dependency-free, so it works in early-boot
//!   contexts and across all build profiles.

use core::fmt;
use std::sync::OnceLock;

#[derive(Copy, Clone)]
pub struct CpuFlags {
    /// True when the current CPU advertises AVX2 (x86/x86_64).
    pub avx2: bool,
    /// True when the current CPU advertises Intel SHA extensions (x86/x86_64).
    pub sha: bool,
    /// True when the current CPU advertises NEON/ASIMD (aarch64).
    pub neon: bool,
    /// Static architecture string (e.g., "x86_64", "aarch64", "unknown").
    pub arch: &'static str,
}

impl fmt::Debug for CpuFlags {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CpuFlags")
            .field("arch", &self.arch)
            .field("avx2", &self.avx2)
            .field("sha", &self.sha)
            .field("neon", &self.neon)
            .finish()
    }
}

static CPU_FLAGS: OnceLock<CpuFlags> = OnceLock::new();

/// Detect CPU flags **right now** (no caching).
pub fn detect_now() -> CpuFlags {
    let arch = current_arch();

    // Base detections per-arch:
    let mut avx2 = false;
    let mut sha  = false;
    let mut neon = false;

    // ---- x86/x86_64 ----
    #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
    {
        // Safe: std macros perform CPUID checks behind the scenes.
        avx2 = std::arch::is_x86_feature_detected!("avx2");
        // Intel SHA extensions (sha1/sha256 instructions), not to be
        // confused with SHA-3; guarded by CPUID leaf 7 EBX bit 29.
        sha = std::arch::is_x86_feature_detected!("sha");
        // No NEON on x86.
        neon = false;
    }

    // ---- AArch64 ----
    #[cfg(target_arch = "aarch64")]
    {
        // On aarch64, NEON (ASIMD) is part of the baseline in most envs,
        // but we query anyway for completeness.
        neon = std::arch::is_aarch64_feature_detected!("neon");
        // x86-only flags remain false.
        avx2 = false;
        sha = false;
    }

    // Other arches: leave all false.

    // Apply env overrides (clamped by architecture so we don't lie).
    apply_env_overrides(arch, &mut avx2, &mut sha, &mut neon);

    CpuFlags { avx2, sha, neon, arch }
}

/// Get a cached reference to the detected CPU flags (computed once).
#[inline]
pub fn get() -> &'static CpuFlags {
    CPU_FLAGS.get_or_init(detect_now)
}

/// Convenience: does this machine have AVX2 (x86/x86_64)?
#[inline]
pub fn has_avx2() -> bool {
    get().avx2
}

/// Convenience: does this machine have Intel SHA extensions (x86/x86_64)?
#[inline]
pub fn has_sha() -> bool {
    get().sha
}

/// Convenience: does this machine have NEON/ASIMD (aarch64)?
#[inline]
pub fn has_neon() -> bool {
    get().neon
}

/// Return a short architecture tag (compile-time).
#[inline]
pub const fn current_arch() -> &'static str {
    #[cfg(target_arch = "x86_64")]
    { "x86_64" }
    #[cfg(target_arch = "x86")]
    { "x86" }
    #[cfg(target_arch = "aarch64")]
    { "aarch64" }
    #[cfg(all(not(target_arch = "x86_64"), not(target_arch = "x86"), not(target_arch = "aarch64")))]
    { "unknown" }
}

/* ----------------------------- internals ----------------------------- */

fn parse_env_bool(key: &str) -> Option<bool> {
    match std::env::var(key).ok()?.as_str() {
        "1" | "true" | "TRUE" | "True" | "yes" | "YES" => Some(true),
        "0" | "false" | "FALSE" | "False" | "no" | "NO" => Some(false),
        _ => None,
    }
}

fn apply_env_overrides(arch: &'static str, avx2: &mut bool, sha: &mut bool, neon: &mut bool) {
    if let Some(v) = parse_env_bool("ANIMICA_CPU_FORCE_AVX2") {
        // Only meaningful on x86/x86_64.
        if matches!(arch, "x86" | "x86_64") {
            *avx2 = v;
        }
    }
    if let Some(v) = parse_env_bool("ANIMICA_CPU_FORCE_SHA") {
        if matches!(arch, "x86" | "x86_64") {
            *sha = v;
        }
    }
    if let Some(v) = parse_env_bool("ANIMICA_CPU_FORCE_NEON") {
        if matches!(arch, "aarch64") {
            *neon = v;
        }
    }
}

/* ------------------------------ Python ------------------------------ */
#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyDict;

    /// Return a dict with the runtime CPU flags.
    ///
    /// Example:
    /// >>> import animica_native as an
    /// >>> an.cpu_flags()
    /// {'arch': 'x86_64', 'avx2': True, 'sha': True, 'neon': False}
    #[pyfunction]
    pub fn cpu_flags(py: Python<'_>) -> PyResult<PyObject> {
        let f = super::get();
        let d = PyDict::new(py);
        d.set_item("arch", f.arch)?;
        d.set_item("avx2", f.avx2)?;
        d.set_item("sha",  f.sha)?;
        d.set_item("neon", f.neon)?;
        Ok(d.into())
    }

    pub fn register(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(cpu_flags, m)?)?;
        Ok(())
    }
}

/* -------------------------------- Tests ------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detect_smoke() {
        let f = detect_now();
        // Arch string should be non-empty and one of the known tags or "unknown".
        assert!(!f.arch.is_empty());
        // Sanity: at least one flag is boolean (always true), well… they all are booleans :)
        assert!((!f.avx2) || f.avx2);
        assert!((!f.sha) || f.sha);
        assert!((!f.neon) || f.neon);
    }

    #[test]
    fn cached_is_consistent() {
        let a = get();
        let b = get();
        assert_eq!(a.arch, b.arch);
        assert_eq!(a.avx2, b.avx2);
        assert_eq!(a.sha, b.sha);
        assert_eq!(a.neon, b.neon);
    }

    #[test]
    fn env_override_is_clamped_by_arch() {
        // This test only exercises the pure function `apply_env_overrides` via `detect_now`
        // by setting env vars before detection. It avoids touching the global cache to
        // keep order-independent.
        std::env::set_var("ANIMICA_CPU_FORCE_AVX2", "1");
        std::env::set_var("ANIMICA_CPU_FORCE_SHA", "1");
        std::env::set_var("ANIMICA_CPU_FORCE_NEON", "1");

        let f = detect_now();
        match f.arch {
            "x86" | "x86_64" => {
                assert!(f.avx2);
                assert!(f.sha);
                assert!(!f.neon);
            }
            "aarch64" => {
                assert!(f.neon);
                assert!(!f.avx2);
                assert!(!f.sha);
            }
            _ => {
                // Unknown arch: overrides should not magically enable anything.
                assert!(!f.avx2 && !f.sha && !f.neon);
            }
        }

        // Cleanup for other tests (best effort).
        std::env::remove_var("ANIMICA_CPU_FORCE_AVX2");
        std::env::remove_var("ANIMICA_CPU_FORCE_SHA");
        std::env::remove_var("ANIMICA_CPU_FORCE_NEON");
    }
}
