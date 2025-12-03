# Data availability parameters

This crate's Reed–Solomon helpers are tuned to the DA defaults used across the
Animica stack. The same numbers appear in `da/constants.py` and the DA service
configuration loader so tests, samplers, and light clients agree on the layout.

## Erasure layout

- **Shard (share) size:** `4096` bytes.
  - Large enough to amortize hashing and NMT overhead while keeping proofs
    compact.
  - Stays well below typical page sizes so shard buffers remain cache-friendly.
- **Shards per stripe:** `64` data + `64` parity (= `128` total).
  - Up to half of the shards can be missing while the blob stays recoverable.
  - Parity density of 50% is chosen to keep sampling proofs small while still
    tolerating adversarial loss and churn.
- **Maximum blob size per block:** `8 MiB` (pre-encoding payload limit).
  - Fits comfortably inside the `64 × 4 KiB = 256 KiB` data capacity per stripe;
    multi-stripe blobs are handled by sharding the payload across successive
    stripes.
  - Keeps per-block proof material bounded and avoids huge parity buffers.

## Sampling

- **Sampling target:** probability of undetected corruption ≤ `2^-40`.
- **Sample count bounds:** between `60` and `256` unique shares per blob by
  default (≈47% of a 128-shard stripe at the lower bound).
  - More samples drive the failure probability down; the upper cap prevents
    over-sampling when networks are congested.
- **Trade-off:** sampling fewer shares reduces client bandwidth/latency but
  raises the chance of missing correlated faults; the defaults balance light
  client cost against a strong detection guarantee.

These values are baked into the DA service defaults and mirrored in the native
RS layout tests so blob encoding/decoding stays aligned with the sampling math.
