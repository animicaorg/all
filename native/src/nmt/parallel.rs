//! Work-splitting helpers for building / hashing NMT layers.
//!
//! Goals:
//! - **Cache-friendly**: pick chunk sizes that fit in L1/L2 and avoid thrashing.
//! - **Tree-friendly**: split into **power-of-two** aligned blocks so each worker
//!   can reduce a local perfect subtree without cross-chunk dependencies.
//! - **Ergonomic**: the parallel runner transparently falls back to sequential
//!   execution when the `rayon` feature is disabled.
//!
//! These utilities are intentionally decoupled from concrete NMT leaf types, so
//! they can be reused across hashers and different layer shapes.

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub struct WorkSplit {
    /// Start index (inclusive) within the logical items space (e.g., leaves).
    pub start: usize,
    /// End index (exclusive).
    pub end: usize,
}

impl WorkSplit {
    #[inline]
    pub const fn len(&self) -> usize {
        self.end - self.start
    }
    #[inline]
    pub const fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Heuristic L1 size in bytes (very conservative default).
/// We prefer **64 KiB** as a sweet spot for mixed uarchs (Apple M-series, x86).
const L1_BYTES_TARGET: usize = 64 * 1024;

/// Round `v` down to the nearest power-of-two (returns at least 1).
#[inline]
fn floor_pow2(v: usize) -> usize {
    if v == 0 {
        return 1;
    }
    1usize << (usize::BITS as usize - 1 - v.leading_zeros() as usize)
}

/// Return `true` iff `v` is a power-of-two.
#[inline]
fn is_pow2(v: usize) -> bool {
    v != 0 && (v & (v - 1)) == 0
}

/// Choose a cache-friendly **maximum** block size (in items) given an item size.
/// This is used as an upper bound for `split_pow2_aligned`.
#[inline]
pub fn choose_max_block_items(elem_size: usize, total_items: usize) -> usize {
    debug_assert!(elem_size > 0);
    if total_items <= 1 {
        return 1;
    }
    // Aim for ~L1_BYTES_TARGET per chunk.
    let ideal = (L1_BYTES_TARGET / elem_size).max(1);
    // Clamp to [1, total_items], then power-of-two round down.
    let clamped = ideal.min(total_items).max(1);
    floor_pow2(clamped)
}

/// Split the **range [0, n)** into power-of-two sized chunks, each aligned to
/// its size, with an upper bound of `max_block` (must be pow2).
///
/// This decomposition enables per-chunk perfect-subtree construction:
/// - Each chunk size is `2^k` and `start % 2^k == 0`.
/// - The concatenation of chunks exactly covers `[0, n)`.
pub fn split_pow2_aligned(n: usize, max_block: usize) -> Vec<WorkSplit> {
    if n == 0 {
        return Vec::new();
    }
    assert!(is_pow2(max_block), "max_block must be a power of two");

    let mut out = Vec::new();
    let mut start = 0usize;

    while start < n {
        let mut size = max_block.min(n - start).max(1);
        // Round size down to the greatest power-of-two <= remaining.
        size = floor_pow2(size);

        // Ensure alignment and fit: enforce (start % size == 0) and size <= rem.
        // If misaligned, or too large for remainder, keep halving.
        while (start & (size - 1)) != 0 || size > (n - start) {
            size >>= 1;
            debug_assert!(size > 0, "size shrank to zero while splitting");
        }

        let end = start + size;
        out.push(WorkSplit { start, end });
        start = end;
    }

    debug_assert_eq!(start, n);
    out
}

/// Convenience: choose a max-block using cache heuristics, then split.
///
/// - `elem_size`: bytes per element
/// - `n`: total elements in the logical space (e.g., leaves)
pub fn split_for_tree(n: usize, elem_size: usize) -> Vec<WorkSplit> {
    let max_block = choose_max_block_items(elem_size, n).max(1);
    split_pow2_aligned(n, max_block)
}

/// Run `f` over each `split` possibly in parallel (if `rayon` feature is on).
///
/// The function `f` must be `Sync + Send`. This is intentionally generic:
/// callers can capture references to the input/output vectors safely as long as
/// they shard by the provided ranges.
pub fn par_for_each<F>(splits: &[WorkSplit], f: F)
where
    F: Fn(WorkSplit) + Sync + Send,
{
    #[cfg(feature = "rayon")]
    {
        use rayon::prelude::*;
        splits.par_iter().for_each(|&w| f(w));
    }
    #[cfg(not(feature = "rayon"))]
    {
        for &w in splits {
            f(w);
        }
    }
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use core::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn floor_pow2_basic() {
        assert_eq!(floor_pow2(0), 1);
        assert_eq!(floor_pow2(1), 1);
        assert_eq!(floor_pow2(2), 2);
        assert_eq!(floor_pow2(3), 2);
        assert_eq!(floor_pow2(4), 4);
        assert_eq!(floor_pow2(5), 4);
        assert_eq!(floor_pow2(63), 32);
        assert_eq!(floor_pow2(64), 64);
        assert_eq!(floor_pow2(65), 64);
    }

    #[test]
    fn choose_block_respects_bounds() {
        // 32B elements: expect ~2k items for 64KiB.
        let m = choose_max_block_items(32, 10_000);
        assert!(is_pow2(m));
        assert!(m <= 10_000);
        assert!(m >= 1);
    }

    #[test]
    fn split_pow2_covers_exactly() {
        for n in [1usize, 2, 3, 7, 8, 9, 15, 16, 31, 32, 1000] {
            let splits = split_pow2_aligned(n, 16);
            let mut covered = 0usize;
            for s in &splits {
                assert!(is_pow2(s.len()), "chunk not power-of-two: {:?}", s);
                assert_eq!(s.start & (s.len() - 1), 0, "misaligned chunk: {:?}", s);
                covered += s.len();
            }
            assert_eq!(covered, n, "did not cover all items for n={}", n);
        }
    }

    #[test]
    fn split_for_tree_uses_cache_heuristic() {
        let n = 50_000;
        let elem_size = 32; // e.g., 32-byte hashes
        let splits = split_for_tree(n, elem_size);
        assert!(!splits.is_empty());
        // Resulting max chunk should not exceed heuristic target.
        let max_chunk = splits.iter().map(|s| s.len()).max().unwrap();
        let heuristic = choose_max_block_items(elem_size, n);
        assert!(max_chunk <= heuristic, "max_chunk={} heuristic={}", max_chunk, heuristic);
    }

    #[test]
    fn par_runner_accumulates_all_ranges() {
        let n = 10_000;
        let splits = split_pow2_aligned(n, 512);
        let acc = AtomicUsize::new(0);
        par_for_each(&splits, |w| {
            // Simulate work: add the chunk length
            acc.fetch_add(w.len(), Ordering::Relaxed);
        });
        assert_eq!(acc.load(Ordering::Relaxed), n);
    }

    #[test]
    fn degenerate_cases() {
        assert!(split_pow2_aligned(0, 1).is_empty());
        let s = split_pow2_aligned(1, 1024);
        assert_eq!(s.len(), 1);
        assert_eq!(s[0], WorkSplit { start: 0, end: 1 });
    }
}
