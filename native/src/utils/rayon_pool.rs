//! Rayon thread-pool utilities.
//!
//! - Optional global pool initialization with sane defaults & env overrides
//! - Local pool guard for scoped parallel regions (no global mutation)
//! - Helpers that gracefully degrade to sequential execution when the `rayon`
//!   feature is disabled (so call sites don't need `cfg` peppered everywhere).
//!
//! Environment variables (used if explicit config not provided):
//! - `ANIMICA_RAYON_THREADS`: integer >= 1 (defaults to available_parallelism)
//! - `ANIMICA_RAYON_STACK`:   per-thread stack size in bytes (optional)
//! - `ANIMICA_RAYON_NAME`:    thread name prefix (default: "animica-w")
//!
//! Notes:
//! - The Rayon **global** pool can only be built once per process. Repeated
//!   calls to `init_global` are benign; if already initialized we treat it as Ok.
//! - Prefer `with_pool` for isolation in libraries to avoid interfering with a
//!   host's global pool configuration.

use crate::error::{NativeError, NativeResult};

/// Configuration for a Rayon thread-pool.
///
/// When `num_threads` is `None`, we'll prefer `ANIMICA_RAYON_THREADS` if set,
/// otherwise `std::thread::available_parallelism()`.
#[derive(Clone, Debug, Default)]
pub struct PoolConfig {
    pub num_threads: Option<usize>,
    pub stack_size: Option<usize>,
    pub thread_name: Option<String>,
}

impl PoolConfig {
    /// Build config reading environment overrides.
    pub fn from_env() -> Self {
        let num_threads = std::env::var("ANIMICA_RAYON_THREADS")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .filter(|&n| n >= 1);

        let stack_size = std::env::var("ANIMICA_RAYON_STACK")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .filter(|&n| n > 0);

        let thread_name = std::env::var("ANIMICA_RAYON_NAME").ok().filter(|s| !s.is_empty());

        PoolConfig { num_threads, stack_size, thread_name }
    }

    /// Determine the effective thread count.
    pub fn effective_threads(&self) -> usize {
        self.num_threads
            .or_else(|| {
                std::env::var("ANIMICA_RAYON_THREADS")
                    .ok()
                    .and_then(|s| s.parse::<usize>().ok())
            })
            .filter(|&n| n >= 1)
            .unwrap_or_else(|| {
                std::thread::available_parallelism()
                    .map(|n| n.get())
                    .unwrap_or(1)
            })
    }

    /// Thread name prefix (defaults to "animica-w").
    pub fn name_prefix(&self) -> String {
        self.thread_name
            .clone()
            .or_else(|| std::env::var("ANIMICA_RAYON_NAME").ok())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "animica-w".to_string())
    }
}

/* -------------------------- Public API -------------------------- */

/// Initialize the Rayon **global** thread pool (idempotent).
///
/// If the global pool is already initialized (by us or someone else), this
/// returns `Ok(())` and leaves the existing pool intact.
pub fn init_global(cfg: &PoolConfig) -> NativeResult<()> {
    init_global_impl(cfg)
}

/// Run `f` inside a **local** thread pool with `num_threads`.
///
/// This does **not** mutate the Rayon global pool. All parallel work spawned
/// inside `f` via `rayon` combinators will use this pool.
///
/// When the `rayon` feature is disabled, `f` simply runs on the current thread.
pub fn with_pool<F, R>(num_threads: usize, name_prefix: Option<&str>, f: F) -> NativeResult<R>
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    with_pool_impl(num_threads, name_prefix, f)
}

/// Maybe-parallel map: if rayon is available **and** `len >= threshold`,
/// map in parallel; otherwise fall back to sequential.
///
/// This is a convenience for data-parallel transforms that are often small.
pub fn maybe_par_map<T, U, F>(input: &[T], threshold: usize, f: F) -> Vec<U>
where
    T: Send + Sync,
    U: Send,
    F: Fn(&T) -> U + Sync,
{
    maybe_par_map_impl(input, threshold, f)
}

/* ------------------ cfg(feature = "rayon") impls ------------------ */

#[cfg(feature = "rayon")]
fn init_global_impl(cfg: &PoolConfig) -> NativeResult<()> {
    use rayon::ThreadPoolBuilder;

    let threads = cfg.effective_threads();
    let name    = cfg.name_prefix();

    // Build a global pool if one hasn't been set already.
    let mut builder = ThreadPoolBuilder::new().num_threads(threads).thread_name(move |i| {
        format!("{}-{:02}", name, i + 1)
    });

    if let Some(sz) = cfg.stack_size {
        builder = builder.stack_size(sz);
    }

    match builder.build_global() {
        Ok(()) => Ok(()),
        Err(_already) => {
            // Global pool was already initialized — treat as success.
            Ok(())
        }
    }
}

#[cfg(not(feature = "rayon"))]
fn init_global_impl(_cfg: &PoolConfig) -> NativeResult<()> {
    // No-op: rayon disabled.
    Ok(())
}

#[cfg(feature = "rayon")]
pub struct PoolGuard {
    pool: rayon::ThreadPool,
}

#[cfg(feature = "rayon")]
impl PoolGuard {
    /// Create a new local pool guard with the given settings.
    pub fn new(num_threads: usize, name_prefix: Option<&str>, stack_size: Option<usize>) -> NativeResult<Self> {
        use rayon::ThreadPoolBuilder;

        if num_threads == 0 {
            return Err(NativeError::InvalidArgument("PoolGuard::new: num_threads must be >= 1"));
        }

        let name = name_prefix.unwrap_or("animica-w-local").to_string();
        let mut builder = ThreadPoolBuilder::new()
            .num_threads(num_threads)
            .thread_name(move |i| format!("{}-{:02}", name, i + 1));

        if let Some(sz) = stack_size {
            builder = builder.stack_size(sz);
        }

        let pool = builder.build().map_err(|_| NativeError::Other("failed to build local rayon pool"))?;
        Ok(Self { pool })
    }

    /// Run a computation inside this pool.
    #[inline]
    pub fn install<F, R>(&self, f: F) -> R
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        self.pool.install(f)
    }
}

#[cfg(feature = "rayon")]
fn with_pool_impl<F, R>(num_threads: usize, name_prefix: Option<&str>, f: F) -> NativeResult<R>
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    let guard = PoolGuard::new(num_threads, name_prefix, None)?;
    Ok(guard.install(f))
}

#[cfg(not(feature = "rayon"))]
fn with_pool_impl<F, R>(_num_threads: usize, _name_prefix: Option<&str>, f: F) -> NativeResult<R>
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    Ok(f())
}

#[cfg(feature = "rayon")]
fn maybe_par_map_impl<T, U, F>(input: &[T], threshold: usize, f: F) -> Vec<U>
where
    T: Send + Sync,
    U: Send,
    F: Fn(&T) -> U + Sync,
{
    use rayon::prelude::*;
    if input.len() >= threshold {
        input.par_iter().map(|t| f(t)).collect()
    } else {
        input.iter().map(|t| f(t)).collect()
    }
}

#[cfg(not(feature = "rayon"))]
fn maybe_par_map_impl<T, U, F>(input: &[T], _threshold: usize, f: F) -> Vec<U>
where
    T: Send + Sync,
    U: Send,
    F: Fn(&T) -> U + Sync,
{
    input.iter().map(|t| f(t)).collect()
}

/* ------------------------------ Tests ------------------------------ */

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_from_env_defaults() {
        // Ensure no panic on env absence.
        let _ = PoolConfig::from_env();
    }

    #[test]
    fn effective_threads_is_nonzero() {
        let cfg = PoolConfig::default();
        assert!(cfg.effective_threads() >= 1);
    }

    #[test]
    fn maybe_par_map_smoke() {
        let v: Vec<u32> = (0..1000).collect();
        let out = maybe_par_map(&v, 16, |x| x + 1);
        assert_eq!(out.len(), v.len());
        assert_eq!(out[0], 1);
        assert_eq!(out[999], 1000);
    }

    #[test]
    fn local_pool_install_works() {
        let res = with_pool(2, Some("test-pool"), || 2 + 3).unwrap();
        assert_eq!(res, 5);
    }

    #[test]
    fn global_init_idempotent() {
        let cfg = PoolConfig::default();
        init_global(&cfg).unwrap();
        init_global(&cfg).unwrap(); // second call should be a no-op
    }
}
