/*
 * Keccak-f[1600] interface (portable C), with a small streaming sponge API.
 *
 * This header exposes:
 *   - The raw permutation: keccakf1600(uint64_t state[25])
 *   - A minimal sponge context over Keccak-f[1600] with pluggable domain
 *     separation byte (delim) and bitrate (rate in bytes).
 *   - One-shot helpers for common digests (Keccak-256 and SHA3-256).
 *
 * Notes
 * -----
 * - The permutation operates on a 5x5 matrix of 64-bit lanes (25 lanes).
 *   The state memory layout here is row-major (x varies fastest):
 *     a[5*y + x] == lane (x, y), for x,y in [0..4].
 * - Endianness: lanes are 64-bit little-endian when absorbing/squeezing bytes,
 *   matching the SHA-3 specification and common implementations.
 * - Domain separation (delim):
 *     * 0x01  -> "Keccak" legacy hash (no padding bit "01" for SHA-3)
 *     * 0x06  -> SHA-3 (FIPS 202)
 *     * 0x1F  -> SHAKE (XOF)
 *   You may pass any application-specific byte if you understand the sponge rules.
 *
 * ABI & FFI
 * ---------
 * - Symbols are exported with default visibility (or __declspec(dllexport) on Win32)
 *   to simplify linking from Rust/C/other languages.
 * - All functions use the C calling convention.
 *
 * License
 * -------
 * - This interface is provided for the Animica project; the corresponding
 *   implementation may be derived from public-domain / permissively licensed
 *   references (e.g., XKCP or tiny-keccak). See native/LICENSE-THIRD-PARTY.md.
 */

#ifndef ANIMICA_NATIVE_C_KECCAK1600_H
#define ANIMICA_NATIVE_C_KECCAK1600_H

/* ---- includes -------------------------------------------------------- */
#include <stddef.h> /* size_t */
#include <stdint.h> /* uint8_t, uint64_t */
#include <stdbool.h>

/* ---- visibility / inlining helpers ---------------------------------- */
#if defined(_WIN32) || defined(__CYGWIN__)
  #if defined(KECCAK1600_BUILD_SHARED)
    #define KECCAK_API __declspec(dllexport)
  #else
    #define KECCAK_API
  #endif
#else
  #define KECCAK_API __attribute__((visibility("default")))
#endif

#if defined(_MSC_VER) && !defined(__clang__)
  #define KECCAK_FORCE_INLINE __forceinline
#else
  #define KECCAK_FORCE_INLINE __attribute__((always_inline)) inline
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---- permutation core ------------------------------------------------ */

/**
 * Apply Keccak-f[1600] permutation to the 1600-bit state (25 x u64 lanes).
 *
 * @param state 25-lane array (little-endian lanes).
 *
 * Safety: state must point to at least 25*8 bytes; the function mutates it.
 */
KECCAK_API void keccakf1600(uint64_t state[25]);

/* ---- sponge context -------------------------------------------------- */

/* Common SHA-3 rates (bytes) for reference. */
enum {
    KECCAK_RATE_SHA3_224 = 144, /* 1152 bits */
    KECCAK_RATE_SHA3_256 = 136, /* 1088 bits */
    KECCAK_RATE_SHA3_384 = 104, /* 832 bits  */
    KECCAK_RATE_SHA3_512 = 72   /* 576 bits  */
};

/* Common domain separators. */
enum {
    KECCAK_DELIM_KECCAK = 0x01, /* legacy Keccak */
    KECCAK_DELIM_SHA3   = 0x06, /* FIPS 202 SHA3-xxx */
    KECCAK_DELIM_SHAKE  = 0x1F  /* SHAKE XOF */
};

/**
 * Streaming sponge context over Keccak-f[1600].
 *
 * rate  : bitrate in BYTES (e.g., 136 for SHA3-256)
 * pos   : current position in the rate portion (0..rate-1)
 * delim : domain separation byte to be applied at finalization
 */
typedef struct keccak1600_ctx {
    uint64_t a[25];   /* 1600-bit state */
    size_t   rate;    /* bytes */
    size_t   pos;     /* [0, rate) */
    uint8_t  delim;   /* domain separation byte */
} keccak1600_ctx;

/**
 * Initialize the sponge with a given rate (in bytes) and domain separator.
 *
 * @param ctx   context to initialize
 * @param rate  number of bytes in the absorb/squeeze rate (e.g., 136)
 * @param delim domain separation byte (e.g., 0x06 for SHA-3)
 */
KECCAK_API void keccak1600_init(keccak1600_ctx *ctx, size_t rate, uint8_t delim);

/**
 * Absorb input bytes into the sponge. Can be called multiple times.
 *
 * @param ctx   initialized context
 * @param in    pointer to input bytes
 * @param inlen number of input bytes
 */
KECCAK_API void keccak1600_absorb(keccak1600_ctx *ctx, const uint8_t *in, size_t inlen);

/**
 * Finalize the sponge (applies domain separator/padding and permutes if needed).
 * After finalization, you may call keccak1600_squeeze() any number of times to
 * extract output bytes; the state will permute on rate boundaries.
 *
 * @param ctx initialized context, not yet finalized
 */
KECCAK_API void keccak1600_finalize(keccak1600_ctx *ctx);

/**
 * Squeeze output bytes from the finalized sponge. Can be called repeatedly to
 * obtain long outputs (e.g., SHAKE).
 *
 * @param ctx     finalized context
 * @param out     destination buffer
 * @param outlen  number of bytes to produce
 */
KECCAK_API void keccak1600_squeeze(keccak1600_ctx *ctx, uint8_t *out, size_t outlen);

/* ---- one-shot helpers ------------------------------------------------ */

/**
 * Keccak-256 (legacy) one-shot hash: 32-byte digest.
 * Equivalent to sponge(rate=136, delim=0x01) with 256-bit output.
 */
KECCAK_API void keccak_256(const uint8_t *in, size_t inlen, uint8_t out[32]);

/**
 * SHA3-256 (FIPS 202) one-shot hash: 32-byte digest.
 * Equivalent to sponge(rate=136, delim=0x06) with 256-bit output.
 */
KECCAK_API void sha3_256(const uint8_t *in, size_t inlen, uint8_t out[32]);

/* Optional: SHA3-224/384/512 helpers (declared for completeness). */
KECCAK_API void sha3_224(const uint8_t *in, size_t inlen, uint8_t out[28]);
KECCAK_API void sha3_384(const uint8_t *in, size_t inlen, uint8_t out[48]);
KECCAK_API void sha3_512(const uint8_t *in, size_t inlen, uint8_t out[64]);

/* ---- tiny inline utilities (headers-only) ---------------------------- */

/* Rotate-left 64 (portable & often inlined). */
static KECCAK_FORCE_INLINE uint64_t keccak_rotl64(uint64_t x, unsigned n) {
#if defined(_MSC_VER) && !defined(__clang__)
    return _rotl64(x, (int)n);
#else
    return (x << (n & 63)) | (x >> ((64 - n) & 63));
#endif
}

/* Zero the state lanes (used by init). Kept inline for convenience. */
static KECCAK_FORCE_INLINE void keccak_state_zero(uint64_t a[25]) {
    /* Unrolled for speed; compilers usually vectorize this well. */
    a[ 0]=0ULL; a[ 1]=0ULL; a[ 2]=0ULL; a[ 3]=0ULL; a[ 4]=0ULL;
    a[ 5]=0ULL; a[ 6]=0ULL; a[ 7]=0ULL; a[ 8]=0ULL; a[ 9]=0ULL;
    a[10]=0ULL; a[11]=0ULL; a[12]=0ULL; a[13]=0ULL; a[14]=0ULL;
    a[15]=0ULL; a[16]=0ULL; a[17]=0ULL; a[18]=0ULL; a[19]=0ULL;
    a[20]=0ULL; a[21]=0ULL; a[22]=0ULL; a[23]=0ULL; a[24]=0ULL;
}

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* ANIMICA_NATIVE_C_KECCAK1600_H */
