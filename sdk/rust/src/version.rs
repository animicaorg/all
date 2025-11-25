//! Crate version helpers and compile-time metadata.

/// Crate semantic version from Cargo.toml.
pub const CRATE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Optional Git description (e.g., "v0.1.0-13-gabcdef1") injected by CI or build.rs.
///
/// Set one of the following environment variables at build time to populate it:
/// - `ANIMICA_GIT_DESCRIBE` (preferred)
/// - `GIT_DESCRIBE`
///
/// Example:
/// `ANIMICA_GIT_DESCRIBE="$(git describe --always --dirty --tags)" cargo build`
pub const GIT_DESCRIBE: Option<&str> = option_env!("ANIMICA_GIT_DESCRIBE")
    .or(option_env!("GIT_DESCRIBE"));

/// Optional build timestamp (RFC3339) injected by CI.
/// Example: `ANIMICA_BUILD_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)" cargo build`
pub const BUILD_TIMESTAMP: Option<&str> = option_env!("ANIMICA_BUILD_TS")
    .or(option_env!("BUILD_TIMESTAMP"));

/// Compile-time target information.
pub const TARGET_TRIPLE: &str = env!("TARGET"); // provided by rustc/cargo
pub const TARGET_OS: &str = env!("CARGO_CFG_TARGET_OS");
pub const TARGET_ARCH: &str = env!("CARGO_CFG_TARGET_ARCH");

/// Enabled cargo features baked into the build.
#[derive(Debug, Clone, Copy)]
pub struct Features {
    pub native: bool,
    pub wasm: bool,
    pub pq: bool,
}

impl Features {
    pub const fn current() -> Self {
        Self {
            native: cfg!(feature = "native"),
            wasm: cfg!(feature = "wasm"),
            pq: cfg!(feature = "pq"),
        }
    }
}

/// Top-level version information.
#[derive(Debug, Clone, Copy)]
pub struct VersionInfo {
    pub crate_version: &'static str,
    pub git_describe: Option<&'static str>,
    pub build_timestamp: Option<&'static str>,
    pub target_triple: &'static str,
    pub target_os: &'static str,
    pub target_arch: &'static str,
    pub features: Features,
}

impl VersionInfo {
    pub const fn new() -> Self {
        Self {
            crate_version: CRATE_VERSION,
            git_describe: GIT_DESCRIBE,
            build_timestamp: BUILD_TIMESTAMP,
            target_triple: TARGET_TRIPLE,
            target_os: TARGET_OS,
            target_arch: TARGET_ARCH,
            features: Features::current(),
        }
    }
}

/// Returns the crate semantic version (e.g., `"0.1.0"`).
#[inline]
pub fn version() -> &'static str {
    CRATE_VERSION
}

/// Returns a human-readable full version string, including git describe (if present),
/// target, and enabled features. Useful for user agents and logs.
pub fn full() -> String {
    let v = version();
    let git = GIT_DESCRIBE.unwrap_or("nogit");
    let feats = Features::current();
    let mut feat_tags = Vec::new();
    if feats.native { feat_tags.push("native"); }
    if feats.wasm { feat_tags.push("wasm"); }
    if feats.pq { feat_tags.push("pq"); }
    let feat_str = if feat_tags.is_empty() { "none" } else { &feat_tags.join("+") };

    format!(
        "animica-sdk/{v} ({git}; {TARGET_TRIPLE}; {TARGET_OS}/{TARGET_ARCH}; features:{feat_str})"
    )
}

/// Returns a concise User-Agent string.
///
/// Example: `animica-sdk/0.1.0 (v0.1.0-13-gabcdef1; x86_64-unknown-linux-gnu; linux/x86_64; features:native+pq)`
#[inline]
pub fn user_agent() -> String {
    full()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_constants_present() {
        assert!(!CRATE_VERSION.is_empty());
        let _ = VersionInfo::new();
        let ua = user_agent();
        assert!(ua.contains("animica-sdk/"));
        assert!(ua.contains(CRATE_VERSION));
    }
}
