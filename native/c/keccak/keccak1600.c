/*
 * Keccak-f[1600] permutation and a compact streaming sponge.
 *
 * Design goals:
 * - Fast single-file C implementation with unrolled rounds
 * - Friendly to FFI (Rust/Python) via simple C ABI
 * - Clean separation between the core permutation and sponge glue
 *
 * See keccak1600.h for API details.
 */

#include "keccak1600.h"
#include <string.h> /* memcpy, memset */

/* ---- permutation ------------------------------------------------------ */
/* Round constants for Keccak-f[1600] (24 rounds). */
static const uint64_t KECCAK_RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL,
    0x800000000000808aULL, 0x8000000080008000ULL,
    0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL,
    0x000000000000008aULL, 0x0000000000000088ULL,
    0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL,
    0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL,
    0x8000000080008081ULL, 0x8000000000008080ULL,
    0x0000000080000001ULL, 0x8000000080008008ULL,
};

/*
 * Highly-tuned, lane-unrolled Keccak-f[1600].
 *
 * Lanes are kept in registers (aXY: X=column, Y=row) and updated in-place.
 * Each round performs: θ, ρ∘π, χ, ι.
 */
KECCAK_API void keccakf1600(uint64_t s[25]) {
    /* Load lanes to registers (x,y) -> aXY. Row-major index: s[5*y + x]. */
    uint64_t a00 = s[ 0], a10 = s[ 1], a20 = s[ 2], a30 = s[ 3], a40 = s[ 4];
    uint64_t a01 = s[ 5], a11 = s[ 6], a21 = s[ 7], a31 = s[ 8], a41 = s[ 9];
    uint64_t a02 = s[10], a12 = s[11], a22 = s[12], a32 = s[13], a42 = s[14];
    uint64_t a03 = s[15], a13 = s[16], a23 = s[17], a33 = s[18], a43 = s[19];
    uint64_t a04 = s[20], a14 = s[21], a24 = s[22], a34 = s[23], a44 = s[24];

    uint64_t b00, b10, b20, b30, b40;
    uint64_t b01, b11, b21, b31, b41;
    uint64_t b02, b12, b22, b32, b42;
    uint64_t b03, b13, b23, b33, b43;
    uint64_t b04, b14, b24, b34, b44;

    uint64_t c0, c1, c2, c3, c4;
    uint64_t d0, d1, d2, d3, d4;

#define KECCAK_ROUND(RC) do {                                            \
    /* θ */                                                              \
    c0 = a00 ^ a01 ^ a02 ^ a03 ^ a04;                                    \
    c1 = a10 ^ a11 ^ a12 ^ a13 ^ a14;                                    \
    c2 = a20 ^ a21 ^ a22 ^ a23 ^ a24;                                    \
    c3 = a30 ^ a31 ^ a32 ^ a33 ^ a34;                                    \
    c4 = a40 ^ a41 ^ a42 ^ a43 ^ a44;                                    \
    d0 = c4 ^ keccak_rotl64(c1, 1);                                      \
    d1 = c0 ^ keccak_rotl64(c2, 1);                                      \
    d2 = c1 ^ keccak_rotl64(c3, 1);                                      \
    d3 = c2 ^ keccak_rotl64(c4, 1);                                      \
    d4 = c3 ^ keccak_rotl64(c0, 1);                                      \
    a00 ^= d0; a01 ^= d0; a02 ^= d0; a03 ^= d0; a04 ^= d0;               \
    a10 ^= d1; a11 ^= d1; a12 ^= d1; a13 ^= d1; a14 ^= d1;               \
    a20 ^= d2; a21 ^= d2; a22 ^= d2; a23 ^= d2; a24 ^= d2;               \
    a30 ^= d3; a31 ^= d3; a32 ^= d3; a33 ^= d3; a34 ^= d3;               \
    a40 ^= d4; a41 ^= d4; a42 ^= d4; a43 ^= d4; a44 ^= d4;               \
                                                                          \
    /* ρ ∘ π */                                                          \
    b00 = a00;                                                            \
    b10 = keccak_rotl64(a01,  1);  b20 = keccak_rotl64(a02, 62);         \
    b30 = keccak_rotl64(a03, 28);  b40 = keccak_rotl64(a04, 27);         \
    b01 = keccak_rotl64(a10, 36);  b11 = keccak_rotl64(a11, 44);         \
    b21 = keccak_rotl64(a12,  6);  b31 = keccak_rotl64(a13, 55);         \
    b41 = keccak_rotl64(a14, 20);                                        \
    b02 = keccak_rotl64(a20,  3);  b12 = keccak_rotl64(a21, 10);         \
    b22 = keccak_rotl64(a22, 43);  b32 = keccak_rotl64(a23, 25);         \
    b42 = keccak_rotl64(a24, 39);                                        \
    b03 = keccak_rotl64(a30, 41);  b13 = keccak_rotl64(a31, 45);         \
    b23 = keccak_rotl64(a32, 15);  b33 = keccak_rotl64(a33, 21);         \
    b43 = keccak_rotl64(a34,  8);                                        \
    b04 = keccak_rotl64(a40, 18);  b14 = keccak_rotl64(a41,  2);         \
    b24 = keccak_rotl64(a42, 61);  b34 = keccak_rotl64(a43, 56);         \
    b44 = keccak_rotl64(a44, 14);                                        \
                                                                          \
    /* χ (row-wise) */                                                   \
    a00 = b00 ^ ((~b10) & b20);                                          \
    a10 = b10 ^ ((~b20) & b30);                                          \
    a20 = b20 ^ ((~b30) & b40);                                          \
    a30 = b30 ^ ((~b40) & b00);                                          \
    a40 = b40 ^ ((~b00) & b10);                                          \
    a01 = b01 ^ ((~b11) & b21);                                          \
    a11 = b11 ^ ((~b21) & b31);                                          \
    a21 = b21 ^ ((~b31) & b41);                                          \
    a31 = b31 ^ ((~b41) & b01);                                          \
    a41 = b41 ^ ((~b01) & b11);                                          \
    a02 = b02 ^ ((~b12) & b22);                                          \
    a12 = b12 ^ ((~b22) & b32);                                          \
    a22 = b22 ^ ((~b32) & b42);                                          \
    a32 = b32 ^ ((~b42) & b02);                                          \
    a42 = b42 ^ ((~b02) & b12);                                          \
    a03 = b03 ^ ((~b13) & b23);                                          \
    a13 = b13 ^ ((~b23) & b33);                                          \
    a23 = b23 ^ ((~b33) & b43);                                          \
    a33 = b33 ^ ((~b43) & b03);                                          \
    a43 = b43 ^ ((~b03) & b13);                                          \
    a04 = b04 ^ ((~b14) & b24);                                          \
    a14 = b14 ^ ((~b24) & b34);                                          \
    a24 = b24 ^ ((~b34) & b44);                                          \
    a34 = b34 ^ ((~b44) & b04);                                          \
    a44 = b44 ^ ((~b04) & b14);                                          \
                                                                          \
    /* ι */                                                              \
    a00 ^= (RC);                                                         \
} while (0)

    /* 24 rounds, fully unrolled to help the compiler keep lanes in regs. */
    KECCAK_ROUND(KECCAK_RC[ 0]);
    KECCAK_ROUND(KECCAK_RC[ 1]);
    KECCAK_ROUND(KECCAK_RC[ 2]);
    KECCAK_ROUND(KECCAK_RC[ 3]);
    KECCAK_ROUND(KECCAK_RC[ 4]);
    KECCAK_ROUND(KECCAK_RC[ 5]);
    KECCAK_ROUND(KECCAK_RC[ 6]);
    KECCAK_ROUND(KECCAK_RC[ 7]);
    KECCAK_ROUND(KECCAK_RC[ 8]);
    KECCAK_ROUND(KECCAK_RC[ 9]);
    KECCAK_ROUND(KECCAK_RC[10]);
    KECCAK_ROUND(KECCAK_RC[11]);
    KECCAK_ROUND(KECCAK_RC[12]);
    KECCAK_ROUND(KECCAK_RC[13]);
    KECCAK_ROUND(KECCAK_RC[14]);
    KECCAK_ROUND(KECCAK_RC[15]);
    KECCAK_ROUND(KECCAK_RC[16]);
    KECCAK_ROUND(KECCAK_RC[17]);
    KECCAK_ROUND(KECCAK_RC[18]);
    KECCAK_ROUND(KECCAK_RC[19]);
    KECCAK_ROUND(KECCAK_RC[20]);
    KECCAK_ROUND(KECCAK_RC[21]);
    KECCAK_ROUND(KECCAK_RC[22]);
    KECCAK_ROUND(KECCAK_RC[23]);

#undef KECCAK_ROUND

    /* Store lanes back to state. */
    s[ 0] = a00; s[ 1] = a10; s[ 2] = a20; s[ 3] = a30; s[ 4] = a40;
    s[ 5] = a01; s[ 6] = a11; s[ 7] = a21; s[ 8] = a31; s[ 9] = a41;
    s[10] = a02; s[11] = a12; s[12] = a22; s[13] = a32; s[14] = a42;
    s[15] = a03; s[16] = a13; s[17] = a23; s[18] = a33; s[19] = a43;
    s[20] = a04; s[21] = a14; s[22] = a24; s[23] = a34; s[24] = a44;
}

/* ---- sponge (streaming) ---------------------------------------------- */

KECCAK_API void keccak1600_init(keccak1600_ctx *ctx, size_t rate, uint8_t delim) {
    keccak_state_zero(ctx->a);
    ctx->rate  = rate;
    ctx->pos   = 0;
    ctx->delim = delim;
}

/* Absorb arbitrary bytes. The rate section is treated as a byte-array view. */
KECCAK_API void keccak1600_absorb(keccak1600_ctx *ctx, const uint8_t *in, size_t inlen) {
    uint8_t *const st = (uint8_t *)ctx->a;
    size_t rate = ctx->rate;
    size_t pos  = ctx->pos;

    if (pos) {
        size_t t = rate - pos;
        if (t > inlen) t = inlen;
        for (size_t i = 0; i < t; i++) st[pos + i] ^= in[i];
        pos += t; in += t; inlen -= t;
        if (pos == rate) {
            keccakf1600(ctx->a);
            pos = 0;
        }
    }

    while (inlen >= rate) {
        /* Fast full-rate absorb (byte XOR). */
        for (size_t i = 0; i < rate; i++) st[i] ^= in[i];
        keccakf1600(ctx->a);
        in += rate; inlen -= rate;
    }

    if (inlen) {
        for (size_t i = 0; i < inlen; i++) st[pos + i] ^= in[i];
        pos += inlen;
    }

    ctx->pos = pos;
}

/* Finalize with domain separator and multi-rate pad10*1, then permute. */
KECCAK_API void keccak1600_finalize(keccak1600_ctx *ctx) {
    uint8_t *const st = (uint8_t *)ctx->a;
    const size_t rate = ctx->rate;

    /* Apply domain separation at current position, then the final 0x80 bit. */
    st[ctx->pos] ^= ctx->delim;
    st[rate - 1] ^= 0x80;
    keccakf1600(ctx->a);
    ctx->pos = 0;
}

/* Squeeze arbitrary-length output (XOF-friendly). */
KECCAK_API void keccak1600_squeeze(keccak1600_ctx *ctx, uint8_t *out, size_t outlen) {
    uint8_t *const st = (uint8_t *)ctx->a;
    size_t rate = ctx->rate;
    size_t pos  = ctx->pos;

    while (outlen) {
        if (pos == rate) {
            keccakf1600(ctx->a);
            pos = 0;
        }
        size_t t = rate - pos;
        if (t > outlen) t = outlen;
        memcpy(out, st + pos, t);
        out += t;
        outlen -= t;
        pos += t;
    }

    ctx->pos = pos;
}

/* ---- one-shot helpers ------------------------------------------------- */

static void sha3_hash_generic(const uint8_t *in, size_t inlen,
                              uint8_t *out, size_t outlen,
                              size_t rate, uint8_t delim) {
    keccak1600_ctx ctx;
    keccak1600_init(&ctx, rate, delim);
    keccak1600_absorb(&ctx, in, inlen);
    keccak1600_finalize(&ctx);
    keccak1600_squeeze(&ctx, out, outlen);
}

KECCAK_API void keccak_256(const uint8_t *in, size_t inlen, uint8_t out[32]) {
    /* Legacy Keccak-256 (delim 0x01), rate 136 bytes. */
    sha3_hash_generic(in, inlen, out, 32, KECCAK_RATE_SHA3_256, KECCAK_DELIM_KECCAK);
}

KECCAK_API void sha3_256(const uint8_t *in, size_t inlen, uint8_t out[32]) {
    sha3_hash_generic(in, inlen, out, 32, KECCAK_RATE_SHA3_256, KECCAK_DELIM_SHA3);
}

KECCAK_API void sha3_224(const uint8_t *in, size_t inlen, uint8_t out[28]) {
    sha3_hash_generic(in, inlen, out, 28, KECCAK_RATE_SHA3_224, KECCAK_DELIM_SHA3);
}

KECCAK_API void sha3_384(const uint8_t *in, size_t inlen, uint8_t out[48]) {
    sha3_hash_generic(in, inlen, out, 48, KECCAK_RATE_SHA3_384, KECCAK_DELIM_SHA3);
}

KECCAK_API void sha3_512(const uint8_t *in, size_t inlen, uint8_t out[64]) {
    sha3_hash_generic(in, inlen, out, 64, KECCAK_RATE_SHA3_512, KECCAK_DELIM_SHA3);
}
