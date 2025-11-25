/*
 * animica_native.h
 * Minimal C ABI surface for Animica Native — hashing, CPU flags, and
 * Reed–Solomon helpers. This mirrors the in-tree C header under
 * native/c/include/animica_native.h and is provided here as the installed
 * public header for external consumers.
 *
 * ABI STABILITY: Experimental (0.x). Names and signatures may change.
 *
 * Memory & Ownership:
 * - Functions suffixed with `_alloc` return heap-allocated memory via `malloc`.
 *   Free with `anm_free()` or `anm_rs_free()` as documented.
 *
 * Thread-safety:
 * - All functions are pure/stateless unless stated otherwise and may be called
 *   concurrently from multiple threads.
 *
 * License: MIT (see repository root)
 */

#ifndef ANIMICA_NATIVE_H
#define ANIMICA_NATIVE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------- */
/*                                   Export                                   */
/* -------------------------------------------------------------------------- */

#if defined(_WIN32) || defined(__CYGWIN__)
  #if defined(ANIMICA_NATIVE_EXPORTS)
    #define ANM_API __declspec(dllexport)
  #else
    #define ANM_API __declspec(dllimport)
  #endif
#else
  #if defined(ANIMICA_NATIVE_EXPORTS)
    #define ANM_API __attribute__((visibility("default")))
  #else
    #define ANM_API
  #endif
#endif

/* -------------------------------------------------------------------------- */
/*                                   Version                                  */
/* -------------------------------------------------------------------------- */

#define ANM_NATIVE_VERSION_MAJOR 0
#define ANM_NATIVE_VERSION_MINOR 1
#define ANM_NATIVE_VERSION_PATCH 0

ANM_API const char *anm_version_string(void); /* "0.1.0" style */

/* -------------------------------------------------------------------------- */
/*                                   Status                                   */
/* -------------------------------------------------------------------------- */

typedef enum anm_status {
  ANM_OK               = 0,
  ANM_ERR_INVALID_ARG  = 1,
  ANM_ERR_UNSUPPORTED  = 2,
  ANM_ERR_NOMEM        = 3,
  ANM_ERR_INTERNAL     = 255
} anm_status;

/* Generic free for memory returned from this library (malloc-family). */
ANM_API void anm_free(void *ptr);

/* -------------------------------------------------------------------------- */
/*                                  CPU Flags                                 */
/* -------------------------------------------------------------------------- */

typedef struct anm_cpu_flags {
  /* x86 */
  uint8_t avx2;    /* 1 if AVX2 is available */
  uint8_t sha_ni;  /* 1 if Intel SHA extensions present */
  /* aarch64 */
  uint8_t neon;    /* 1 if NEON is available */
  uint8_t sha3;    /* 1 if SHA3 instructions present (ARMv8.2) */
  /* reserved for future fields */
  uint8_t _rsvd[4];
} anm_cpu_flags;

/* Detect runtime CPU feature flags. 'out' must be non-NULL. */
ANM_API void anm_cpu_detect(anm_cpu_flags *out);

/* -------------------------------------------------------------------------- */
/*                                   Hashing                                  */
/* -------------------------------------------------------------------------- */
/* All digests are 32-byte (256-bit) outputs. 'out32' must point to 32 bytes. */

ANM_API anm_status anm_blake3(const uint8_t *data, size_t len, uint8_t out32[32]);

ANM_API anm_status anm_keccak256(const uint8_t *data, size_t len, uint8_t out32[32]);

ANM_API anm_status anm_sha256(const uint8_t *data, size_t len, uint8_t out32[32]);

/* -------------------------------------------------------------------------- */
/*                             Reed–Solomon (RS)                              */
/* -------------------------------------------------------------------------- */
/*
 * High-level convenience API for RS encoding and reconstruction.
 * The codec uses a systematic layout: the first K shards are data, followed by
 * M parity shards. All shards have equal length (shard_len).
 *
 * anm_rs_expected_shard_len:
 *   Compute the encoded shard size for the given input length and K data shards.
 *
 * anm_rs_encode_alloc:
 *   Allocates (K+M) shard buffers (each shard_len bytes) and fills them with
 *   the encoded data (systematic). The caller takes ownership of the returned
 *   array-of-pointers. Free the array and its contents with anm_rs_free().
 *
 * anm_rs_reconstruct:
 *   In-place repair of missing shards. Provide an array of (K+M) pointers;
 *   set missing shards to NULL; present shards must be of length shard_len.
 *   On success (ANM_OK) all NULLs will be replaced with valid, allocated
 *   buffers (caller now owns them) and the array entries will be non-NULL.
 *
 * anm_rs_free:
 *   Free shards allocated by encode/reconstruct. Pass the same 'total' (K+M).
 */

/* Compute the per-shard length for an input of data_len split into K shards. */
ANM_API size_t anm_rs_expected_shard_len(size_t data_len, uint32_t k);

/* Encode: allocate and produce K+M shards. */
ANM_API anm_status
anm_rs_encode_alloc(const uint8_t *data,
                    size_t data_len,
                    uint32_t k,                 /* data shards */
                    uint32_t m,                 /* parity shards */
                    uint8_t ***shards_out,      /* out: array of (k+m) pointers */
                    size_t *shard_len_out);     /* out: bytes per shard */

/* Reconstruct in place: fills NULL entries with newly allocated shard buffers. */
ANM_API anm_status
anm_rs_reconstruct(uint8_t **shards,          /* in/out: array of (k+m) pointers */
                   uint32_t k,
                   uint32_t m,
                   size_t shard_len);

/* Free a shard array (and each shard buffer) allocated by this library. */
ANM_API void anm_rs_free(uint8_t **shards, uint32_t total);

/* -------------------------------------------------------------------------- */
/*                             Namespaced Merkle (NMT)                        */
/* -------------------------------------------------------------------------- */
/*
 * Minimal NMT surface (experimental). Leaves carry a fixed-size namespace id.
 * The concrete leaf encoding (ns || len || data) mirrors the Rust implementation.
 * For many applications only the root computation is needed.
 */

#define ANM_NMT_NS_LEN 8  /* 64-bit namespace ids */

typedef struct anm_nmt_leaf {
  const uint8_t *ns;       /* exactly ANM_NMT_NS_LEN bytes */
  const uint8_t *data;     /* arbitrary payload */
  size_t data_len;
} anm_nmt_leaf;

/* Compute NMT root over 'n_leaves' leaves. 'out32' must be 32 bytes. */
ANM_API anm_status
anm_nmt_root(const anm_nmt_leaf *leaves, size_t n_leaves, uint8_t out32[32]);

/*
 * Verify a single-inclusion proof. The 'proof' format mirrors the Rust/Python
 * library (length-delimited hops with left/right markers). This is intentionally
 * opaque here; callers should construct proofs using the native API that
 * produced them. Returns ANM_OK if verification succeeds.
 */
ANM_API anm_status
anm_nmt_verify(const uint8_t *proof, size_t proof_len,
               const uint8_t ns[ANM_NMT_NS_LEN],
               const uint8_t *data, size_t data_len,
               const uint8_t root32[32]);

/* -------------------------------------------------------------------------- */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* ANIMICA_NATIVE_H */
