//! Shard layout, padding, and length/alignment planning for Reed–Solomon.
//!
//! This module decides **how payload bytes are mapped to shards** for RS(k+m,k)
//! erasure coding, and provides helpers to:
//! - compute per-shard length given a payload size and alignment constraint,
//! - split (shardify) a payload into `k` equal-length data shards (plus `m`
//!   zero-filled parity slots ready for encoding),
//! - reassemble (unshard) the original payload bytes from the first `k` shards,
//!   trimming any tail padding added for alignment.
//!
//! ## Layout
//! We use the **row-wise/systematic layout**: the payload is split into `k`
//! contiguous segments of size `shard_len` (except the last which is padded).
//! Parity is computed across shards *column-wise* (same offset in each shard).
//!
//! ```text
//! payload ───────────────────────────────────────────────────────────▶
//! ┌────────────┬────────────┬────────────┬────────────┐
//! │  shard 0   │  shard 1   │   ...      │  shard k-1 │  (k data shards)
//! └────────────┴────────────┴────────────┴────────────┘
//!        ↑             ↑                        ↑
//!        equal-length shards (padded to `shard_len`, alignment = A)
//! ```
//!
//! Padding ensures each shard length is a multiple of `align` (default 64),
//! which improves SIMD/cache behavior for backends. Pointer alignment is left
//! to the allocator; we only guarantee **length** alignment.
//!
//! This module is backend-agnostic and can be used with either the pure-Rust
//! or ISA-L RS codec implementations.

use core::fmt;

/// Default length-alignment (bytes) for shard buffers.
/// 64 is a reasonable minimum (cache line); SIMD backends often benefit.
pub const DEFAULT_ALIGN: usize = 64;

/// Plan describing how a payload will be sharded for RS(k+m, k).
#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub struct Layout {
    /// Number of data shards (k).
    pub data_shards: usize,
    /// Number of parity shards (m).
    pub parity_shards: usize,
    /// Per-shard length after padding/alignment.
    pub shard_len: usize,
    /// Original payload length (unpadded).
    pub payload_len: usize,
    /// Padding added across the `k` data shards in total.
    pub padding_len: usize,
    /// Alignment (length multiple) used.
    pub align: usize,
}

impl Layout {
    /// Construct a layout for a payload of `payload_len` bytes split across `k`
    /// data shards with `m` parity shards, using the given `align` (use 0 or 1
    /// to disable alignment).
    pub fn new(payload_len: usize, k: usize, m: usize, align: usize) -> Result<Self, LayoutError> {
        if k == 0 {
            return Err(LayoutError::InvalidArg("data_shards (k) must be > 0"));
        }
        if m == 0 {
            return Err(LayoutError::InvalidArg("parity_shards (m) must be > 0"));
        }
        let align = if align == 0 { 1 } else { align.next_power_of_two() };

        let base = ceil_div(payload_len, k);
        let shard_len = align_up(base, align);

        let padded_total = shard_len
            .checked_mul(k)
            .ok_or(LayoutError::Overflow)?;
        let padding_len = padded_total
            .checked_sub(payload_len)
            .ok_or(LayoutError::Overflow)?;

        Ok(Self {
            data_shards: k,
            parity_shards: m,
            shard_len,
            payload_len,
            padding_len,
            align,
        })
    }

    /// Convenience: construct with [`DEFAULT_ALIGN`].
    #[inline]
    pub fn with_default_align(payload_len: usize, k: usize, m: usize) -> Result<Self, LayoutError> {
        Self::new(payload_len, k, m, DEFAULT_ALIGN)
    }

    /// Total number of shards (`k + m`).
    #[inline]
    pub const fn total_shards(&self) -> usize {
        self.data_shards + self.parity_shards
    }

    /// Total padded data length (`k * shard_len`).
    #[inline]
    pub fn padded_data_len(&self) -> usize {
        self.data_shards * self.shard_len
    }

    /// Return `(start, end)` byte range of the payload that maps into data shard `i` (0..k),
    /// **before** padding. Useful for debugging or partial writes.
    #[inline]
    pub fn payload_range_for_data_shard(&self, i: usize) -> Result<(usize, usize), LayoutError> {
        if i >= self.data_shards {
            return Err(LayoutError::InvalidArg("data shard index out of range"));
        }
        let start = i * self.shard_len;
        let end = core::cmp::min(start + self.shard_len, self.payload_len);
        Ok((start, end))
    }

    /// Split `payload` into `k` data shards and `m` zeroed parity shards, all
    /// of length `shard_len`. Returns a vector of length `k+m` suitable to be
    /// passed to an encoder in-place.
    pub fn shardify(&self, payload: &[u8]) -> Result<Vec<Vec<u8>>, LayoutError> {
        if payload.len() != self.payload_len {
            return Err(LayoutError::InvalidArg("payload length mismatch"));
        }
        let mut shards: Vec<Vec<u8>> = Vec::with_capacity(self.total_shards());

        // Fill data shards with contiguous slices of the payload; pad tail with zeros.
        let mut offset = 0usize;
        for _ in 0..self.data_shards {
            let remaining = payload.len().saturating_sub(offset);
            let take = remaining.min(self.shard_len);
            let mut shard = Vec::with_capacity(self.shard_len);
            // Safety: we immediately set_len after writing exact number of bytes.
            shard.extend_from_slice(&payload[offset..offset + take]);
            shard.resize(self.shard_len, 0);
            shards.push(shard);
            offset = offset.saturating_add(take);
        }

        // Parity shards are empty/zero-initialized; encode step will fill them.
        for _ in 0..self.parity_shards {
            shards.push(vec![0u8; self.shard_len]);
        }

        debug_assert_eq!(shards.len(), self.total_shards());
        Ok(shards)
    }

    /// Reassemble the original payload from the **first `k` shards** by
    /// concatenation and trimming to the original `payload_len`. This assumes
    /// the shards are consistent and of uniform length `shard_len`.
    pub fn unshard(&self, shards: &[Vec<u8>]) -> Result<Vec<u8>, LayoutError> {
        if shards.len() < self.data_shards {
            return Err(LayoutError::InvalidArg("not enough shards to unshard"));
        }
        for (i, s) in shards[..self.data_shards].iter().enumerate() {
            if s.len() != self.shard_len {
                return Err(LayoutError::ShardLenMismatch { index: i, len: s.len(), expected: self.shard_len });
            }
        }

        let mut out = Vec::with_capacity(self.padded_data_len());
        for s in &shards[..self.data_shards] {
            out.extend_from_slice(s);
        }
        out.truncate(self.payload_len);
        Ok(out)
    }

    /// Validate a shards vector matches this layout (length, counts, alignment of lengths).
    pub fn validate_shards(&self, shards: &[Vec<u8>]) -> Result<(), LayoutError> {
        if shards.len() != self.total_shards() {
            return Err(LayoutError::InvalidArg("shards.len() must equal k + m"));
        }
        for (i, s) in shards.iter().enumerate() {
            if s.len() != self.shard_len {
                return Err(LayoutError::ShardLenMismatch { index: i, len: s.len(), expected: self.shard_len });
            }
        }
        Ok(())
    }
}

/// Errors produced by the layout planner/sharder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LayoutError {
    InvalidArg(&'static str),
    ShardLenMismatch { index: usize, len: usize, expected: usize },
    Overflow,
}

impl fmt::Display for LayoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use LayoutError::*;
        match self {
            InvalidArg(s) => write!(f, "invalid argument: {s}"),
            ShardLenMismatch { index, len, expected } => {
                write!(f, "shard[{index}] length {len} != expected {expected}")
            }
            Overflow => write!(f, "size computation overflow"),
        }
    }
}
impl std::error::Error for LayoutError {}

#[inline]
pub const fn ceil_div(n: usize, d: usize) -> usize {
    // d>0 by construction
    (n + d - 1) / d
}

#[inline]
pub const fn align_up(n: usize, align: usize) -> usize {
    let a = if align == 0 { 1 } else { align };
    (n + (a - 1)) & !(a - 1)
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use rand::{rngs::StdRng, Rng, SeedableRng};

    #[test]
    fn ceil_div_basic() {
        assert_eq!(ceil_div(0, 5), 0);
        assert_eq!(ceil_div(1, 5), 1);
        assert_eq!(ceil_div(5, 5), 1);
        assert_eq!(ceil_div(6, 5), 2);
        assert_eq!(ceil_div(9, 5), 2);
        assert_eq!(ceil_div(10, 5), 2);
        assert_eq!(ceil_div(11, 5), 3);
    }

    #[test]
    fn align_up_basic() {
        assert_eq!(align_up(0, 64), 0);
        assert_eq!(align_up(1, 64), 64);
        assert_eq!(align_up(64, 64), 64);
        assert_eq!(align_up(65, 64), 128);
        assert_eq!(align_up(127, 64), 128);
        assert_eq!(align_up(128, 64), 128);
    }

    #[test]
    fn plan_and_shard_roundtrip() {
        let mut rng = StdRng::seed_from_u64(42);
        for _ in 0..50 {
            let k = 3 + (rng.gen::<usize>() % 6); // 3..8
            let m = 2 + (rng.gen::<usize>() % 4); // 2..5
            let len = rng.gen_range(0..50_000);
            let align = [1, 16, 32, 64, 128][rng.gen::<usize>() % 5];

            let layout = Layout::new(len, k, m, align).unwrap();
            assert_eq!(layout.padded_data_len() % k, 0);
            assert_eq!(layout.shard_len % layout.align, 0);

            let mut payload = vec![0u8; len];
            rng.fill(&mut payload[..]);

            let shards = layout.shardify(&payload).unwrap();
            layout.validate_shards(&shards).unwrap();

            // First k shards must be equal length and concatenation should
            // reconstruct original after trimming.
            let back = layout.unshard(&shards).unwrap();
            assert_eq!(back, payload);
        }
    }

    #[test]
    fn ranges_cover_payload() {
        let layout = Layout::with_default_align(10_000, 7, 3).unwrap();
        let mut covered = 0usize;
        for i in 0..layout.data_shards {
            let (s, e) = layout.payload_range_for_data_shard(i).unwrap();
            if e > s {
                covered += e - s;
            }
            assert!(e - s <= layout.shard_len);
        }
        assert_eq!(covered, layout.payload_len);
    }

    #[test]
    fn mismatch_len_detected() {
        let layout = Layout::with_default_align(4096, 4, 2).unwrap();
        let bad = vec![vec![0u8; layout.shard_len + 1]; layout.total_shards()];
        let err = layout.validate_shards(&bad).unwrap_err();
        matches!(err, LayoutError::ShardLenMismatch { .. });
    }
}
