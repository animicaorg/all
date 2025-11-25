/*
 * animica_native.h
 * Minimal C ABI for Animica Native (future-facing; intentionally small).
 *
 * This header exposes a stable subset focused on:
 *   - Version & feature discovery
 *   - CPU feature reporting
 *   - Fast hash functions (BLAKE3, Keccak-256, SHA-256)
 *
 * Design goals:
 *   - C89-compatible signatures (uses stdint.h / stddef.h types)
 *   - No heap ownership semantics cross the FFI boundary
 *   - Callers own input/output buffers; functions write into caller-provided memory
 *   - Return codes are small integers; 0 == success
 *
 * Thread-safety:
 *   - All functions are thread-safe and reentrant.
 *
 * Binary compatibility:
 *   - Subject to semantic versioning via the version macros below.
 *   - New functions may be appended; existing signatures will not change in a minor/patch release.
 */

#ifndef ANIMICA_NATIVE_H
#define ANIMICA_NATIVE_H

/* --- Standard headers --- */
#include <stddef.h>  /* size_t */
#include <stdint.h>  /* uint8_t, uint32_t */

/* --- API export control (Windows / Unixlike) --- */
#if defined(_WIN32) || defined(_WIN64)
  #if defined(ANIMICA_NATIVE_BUILD)
    #define ANIMICA_NATIVE_API __declspec(dllexport)
  #else
    #define ANIMICA_NATIVE_API __declspec(dllimport)
  #endif
  #define ANIMICA_NATIVE_CALL __cdecl
#else
  #if defined(__GNUC__) && __GNUC__ >= 4
    #define ANIMICA_NATIVE_API __attribute__((visibility("default")))
  #else
    #define ANIMICA_NATIVE_API
  #endif
  #define ANIMICA_NATIVE_CALL /* default cdecl */
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------- */
/* Versioning                                                                 */
/* -------------------------------------------------------------------------- */

/* Library semantic version (compiled-in) */
#define ANIMICA_NATIVE_VERSION_MAJOR 0u
#define ANIMICA_NATIVE_VERSION_MINOR 1u
#define ANIMICA_NATIVE_VERSION_PATCH 0u

/* Hash output sizes (bytes) */
#define ANIMICA_LEN_BLAKE3_256 32u
#define ANIMICA_LEN_KECCAK256  32u
#define ANIMICA_LEN_SHA256     32u

/* Error codes (0 == success) */
typedef enum animica_native_error_e {
  ANIMICA_OK          = 0,   /* success */
  ANIMICA_ERR_NULL    = 1,   /* null pointer provided where non-null required */
  ANIMICA_ERR_BADLEN  = 2,   /* invalid length or size mismatch */
  ANIMICA_ERR_UNSUP   = 3,   /* operation unsupported on this build/CPU */
  ANIMICA_ERR_INTERNAL= 255  /* unexpected internal error */
} animica_native_error_t;

/**
 * Returns a NUL-terminated version string (e.g., "0.1.0").
 * Lifetime is static; do not free.
 */
ANIMICA_NATIVE_API
const char* ANIMICA_NATIVE_CALL animica_native_version_string(void);

/**
 * Writes (major, minor, patch). Any of the pointers may be NULL.
 */
ANIMICA_NATIVE_API
void ANIMICA_NATIVE_CALL animica_native_version(uint32_t* major,
                                                uint32_t* minor,
                                                uint32_t* patch);

/* -------------------------------------------------------------------------- */
/* CPU feature discovery                                                       */
/* -------------------------------------------------------------------------- */

typedef struct animica_cpu_features_s {
  /* x86/x86_64 */
  uint8_t x86_avx2;   /* 1 if AVX2 available */
  uint8_t x86_sha;    /* 1 if Intel SHA extensions available */
  /* aarch64 */
  uint8_t arm_neon;   /* 1 if NEON available */
  uint8_t arm_sha3;   /* 1 if ARMv8.2 SHA3/Keccak available */
  /* reserved for future flags */
  uint8_t reserved[4];
} animica_cpu_features_t;

/**
 * Returns CPU feature flags detected at runtime.
 * Pure value; safe to call frequently.
 */
ANIMICA_NATIVE_API
animica_cpu_features_t ANIMICA_NATIVE_CALL animica_cpu_get_features(void);

/* -------------------------------------------------------------------------- */
/* Hashing                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * BLAKE3-256 hash.
 * - data: pointer to input bytes (may be NULL iff len==0)
 * - len:  number of input bytes
 * - out32: pointer to a 32-byte buffer for the digest (must be non-NULL)
 *
 * Returns ANIMICA_OK on success, error code otherwise.
 */
ANIMICA_NATIVE_API
int32_t ANIMICA_NATIVE_CALL animica_blake3_hash(const void* data,
                                                size_t len,
                                                uint8_t out32[ANIMICA_LEN_BLAKE3_256]);

/**
 * Keccak-256 (Ethereum-style, no SHA-3 padding domain separation).
 * - data/len as above
 * - out32: 32-byte buffer for digest
 */
ANIMICA_NATIVE_API
int32_t ANIMICA_NATIVE_CALL animica_keccak256(const void* data,
                                              size_t len,
                                              uint8_t out32[ANIMICA_LEN_KECCAK256]);

/**
 * SHA-256 (FIPS 180-4).
 * - data/len as above
 * - out32: 32-byte buffer for digest
 */
ANIMICA_NATIVE_API
int32_t ANIMICA_NATIVE_CALL animica_sha256(const void* data,
                                           size_t len,
                                           uint8_t out32[ANIMICA_LEN_SHA256]);

/* -------------------------------------------------------------------------- */
/* Capability discovery (build-time feature toggles)                           */
/* -------------------------------------------------------------------------- */

/**
 * Returns 1 if Reed–Solomon acceleration backends are compiled in
 * (e.g., ISA-L or portable fallback), 0 otherwise.
 * Note: API surface for RS/NMT is intentionally not exposed here yet.
 */
ANIMICA_NATIVE_API
int32_t ANIMICA_NATIVE_CALL animica_feature_rs_available(void);

/**
 * Returns 1 if the Keccak fastpath (optional C kernel) is compiled in, else 0.
 */
ANIMICA_NATIVE_API
int32_t ANIMICA_NATIVE_CALL animica_feature_c_keccak_available(void);

/* -------------------------------------------------------------------------- */
/* Usage notes                                                                 */
/* -------------------------------------------------------------------------- */
/*
 * Example:
 *
 *   #include "animica_native.h"
 *   #include <stdio.h>
 *
 *   int main(void) {
 *     uint8_t out[ANIMICA_LEN_BLAKE3_256];
 *     const char* msg = "hello";
 *     if (animica_blake3_hash(msg, 5, out) != ANIMICA_OK) return 1;
 *     for (size_t i = 0; i < sizeof(out); ++i) printf("%02x", out[i]);
 *     printf("\n");
 *     return 0;
 *   }
 */

/* -------------------------------------------------------------------------- */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* ANIMICA_NATIVE_H */
