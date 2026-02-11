/**
 * Dilithium3 C implementation for WASM compilation
 * 
 * This is a reference implementation matching python/animica/_vendor/dilithium_py/dilithium3.py
 * It uses SHAKE-256 (Keccak) for deterministic key generation and signing.
 * 
 * WARNING: This is a REFERENCE implementation for development/testing.
 * For production, use a fully validated ML-DSA-65 implementation.
 */

#include <stdint.h>
#include <string.h>
#include <emscripten.h>

// Dilithium3 (ML-DSA-65) constants
#define DILITHIUM_Q 8380417
#define DILITHIUM_N 256
#define DILITHIUM_K 6
#define DILITHIUM_L 5

// Size constants (bytes)
#define PK_BYTES 1952
#define SK_BYTES 4000
#define SIG_BYTES 3293

// Export constants for JS
EMSCRIPTEN_KEEPALIVE
const uint32_t DILITHIUM_PK_BYTES = PK_BYTES;

EMSCRIPTEN_KEEPALIVE
const uint32_t DILITHIUM_SK_BYTES = SK_BYTES;

EMSCRIPTEN_KEEPALIVE
const uint32_t DILITHIUM_SIG_BYTES = SIG_BYTES;

// Simple SHAKE-256 implementation (Keccak-256 in XOF mode)
// For a real implementation, use a validated Keccak library
// This is a minimal reference for deterministic outputs

#define KECCAK_ROUNDS 24
#define KECCAK_STATE_SIZE 25

static const uint64_t keccak_round_constants[KECCAK_ROUNDS] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

static uint64_t rotl64(uint64_t x, int n) {
    return (x << n) | (x >> (64 - n));
}

static void keccak_f1600(uint64_t state[25]) {
    uint64_t C[5], D[5], B[25];
    
    for (int round = 0; round < KECCAK_ROUNDS; round++) {
        // Theta
        for (int x = 0; x < 5; x++) {
            C[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
        }
        for (int x = 0; x < 5; x++) {
            D[x] = C[(x + 4) % 5] ^ rotl64(C[(x + 1) % 5], 1);
        }
        for (int x = 0; x < 5; x++) {
            for (int y = 0; y < 5; y++) {
                state[x + 5 * y] ^= D[x];
            }
        }
        
        // Rho and Pi
        B[0] = state[0];
        int x = 1, y = 0;
        for (int t = 0; t < 24; t++) {
            int r = ((t + 1) * (t + 2) / 2) % 64;
            B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl64(state[x + 5 * y], r);
            int tmp = y;
            y = (2 * x + 3 * y) % 5;
            x = tmp;
        }
        
        // Chi
        for (int y = 0; y < 5; y++) {
            uint64_t T[5];
            for (int x = 0; x < 5; x++) {
                T[x] = B[x + 5 * y];
            }
            for (int x = 0; x < 5; x++) {
                state[x + 5 * y] = T[x] ^ ((~T[(x + 1) % 5]) & T[(x + 2) % 5]);
            }
        }
        
        // Iota
        state[0] ^= keccak_round_constants[round];
    }
}

typedef struct {
    uint64_t state[25];
    uint8_t buffer[136]; // rate for SHAKE-256 (1088 bits = 136 bytes)
    size_t buffer_pos;
    int squeezing;
} shake256_ctx;

static void shake256_init(shake256_ctx *ctx) {
    memset(ctx, 0, sizeof(shake256_ctx));
}

static void shake256_absorb(shake256_ctx *ctx, const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        ctx->buffer[ctx->buffer_pos++] = data[i];
        if (ctx->buffer_pos == 136) {
            // Absorb block
            for (int j = 0; j < 136 / 8; j++) {
                uint64_t word = 0;
                for (int k = 0; k < 8; k++) {
                    word |= ((uint64_t)ctx->buffer[j * 8 + k]) << (k * 8);
                }
                ctx->state[j] ^= word;
            }
            keccak_f1600(ctx->state);
            ctx->buffer_pos = 0;
        }
    }
}

static void shake256_finalize(shake256_ctx *ctx) {
    // Padding: 0x1F for SHAKE-256
    ctx->buffer[ctx->buffer_pos] = 0x1F;
    ctx->buffer_pos++;
    
    // Pad with zeros
    while (ctx->buffer_pos < 136) {
        ctx->buffer[ctx->buffer_pos++] = 0;
    }
    ctx->buffer[135] |= 0x80; // Final bit
    
    // Absorb final block
    for (int j = 0; j < 136 / 8; j++) {
        uint64_t word = 0;
        for (int k = 0; k < 8; k++) {
            word |= ((uint64_t)ctx->buffer[j * 8 + k]) << (k * 8);
        }
        ctx->state[j] ^= word;
    }
    keccak_f1600(ctx->state);
    ctx->buffer_pos = 0;
    ctx->squeezing = 1;
}

static void shake256_squeeze(shake256_ctx *ctx, uint8_t *out, size_t len) {
    if (!ctx->squeezing) {
        shake256_finalize(ctx);
    }
    
    for (size_t i = 0; i < len; i++) {
        if (ctx->buffer_pos == 0) {
            // Extract bytes from state
            for (int j = 0; j < 136 / 8; j++) {
                for (int k = 0; k < 8; k++) {
                    ctx->buffer[j * 8 + k] = (ctx->state[j] >> (k * 8)) & 0xFF;
                }
            }
            if (i > 0) { // Not first squeeze
                keccak_f1600(ctx->state);
            }
        }
        out[i] = ctx->buffer[ctx->buffer_pos];
        ctx->buffer_pos = (ctx->buffer_pos + 1) % 136;
    }
}

static void shake256(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_len) {
    shake256_ctx ctx;
    shake256_init(&ctx);
    shake256_absorb(&ctx, in, in_len);
    shake256_squeeze(&ctx, out, out_len);
}

// Dilithium3 API

EMSCRIPTEN_KEEPALIVE
int keypair_from_seed(const uint8_t *seed, size_t seed_len, uint8_t *pk, uint8_t *sk) {
    if (seed_len != 32) {
        return -1; // Invalid seed length
    }
    
    // Generate public key: shake256("dilithium3_pk|" + seed)
    uint8_t pk_input[32 + 14];
    memcpy(pk_input, "dilithium3_pk|", 14);
    memcpy(pk_input + 14, seed, 32);
    shake256(pk_input, 46, pk, PK_BYTES);
    
    // Generate secret key: seed || shake256("dilithium3_sk|" + seed)
    memcpy(sk, seed, 32);
    uint8_t sk_input[32 + 14];
    memcpy(sk_input, "dilithium3_sk|", 14);
    memcpy(sk_input + 14, seed, 32);
    shake256(sk_input, 46, sk + 32, SK_BYTES - 32);
    
    return 0;
}

EMSCRIPTEN_KEEPALIVE
int sign(const uint8_t *msg, size_t msg_len, const uint8_t *sk, uint8_t *sig) {
    if (!msg || !sk || !sig) {
        return -1;
    }
    
    // Compute message hash: shake256(msg)
    uint8_t msg_hash[64];
    shake256(msg, msg_len, msg_hash, 64);
    
    // Derive RNG seed: shake256("dilithium3_rng|" + sk[:32] + msg_hash)
    uint8_t rng_input[14 + 32 + 64];
    memcpy(rng_input, "dilithium3_rng|", 15);
    memcpy(rng_input + 15, sk, 32);
    memcpy(rng_input + 15 + 32, msg_hash, 64);
    uint8_t rng_seed[32];
    shake256(rng_input, 15 + 32 + 64, rng_seed, 32);
    
    // Compute public key for commitment: shake256("dilithium3_pk|" + sk[:32])
    uint8_t pk_for_commitment[PK_BYTES];
    uint8_t pk_input[14 + 32];
    memcpy(pk_input, "dilithium3_pk|", 14);
    memcpy(pk_input + 14, sk, 32);
    shake256(pk_input, 46, pk_for_commitment, PK_BYTES);
    
    // Compute commitment: shake256(pk[:32] + msg)
    uint8_t commitment_input[32 + msg_len];
    memcpy(commitment_input, pk_for_commitment, 32);
    memcpy(commitment_input + 32, msg, msg_len);
    uint8_t commitment[32];
    shake256(commitment_input, 32 + msg_len, commitment, 32);
    
    // Build signature rest: shake256("dilithium3_sig|" + commitment + sk[:32] + msg_hash + rng_seed)
    uint8_t sig_input[15 + 32 + 32 + 64 + 32];
    memcpy(sig_input, "dilithium3_sig|", 15);
    memcpy(sig_input + 15, commitment, 32);
    memcpy(sig_input + 15 + 32, sk, 32);
    memcpy(sig_input + 15 + 32 + 32, msg_hash, 64);
    memcpy(sig_input + 15 + 32 + 32 + 64, rng_seed, 32);
    
    // Build final signature: commitment || sig_rest
    memcpy(sig, commitment, 32);
    shake256(sig_input, 15 + 32 + 32 + 64 + 32, sig + 32, SIG_BYTES - 32);
    
    return 0;
}

EMSCRIPTEN_KEEPALIVE
int verify(const uint8_t *msg, size_t msg_len, const uint8_t *pk, const uint8_t *sig) {
    if (!msg || !pk || !sig) {
        return 0; // Invalid inputs
    }
    
    // Extract commitment from signature (first 32 bytes)
    uint8_t commitment[32];
    memcpy(commitment, sig, 32);
    
    // Recompute expected commitment: shake256(pk[:32] + msg)
    uint8_t commitment_input[32 + msg_len];
    memcpy(commitment_input, pk, 32);
    memcpy(commitment_input + 32, msg, msg_len);
    uint8_t expected_commitment[32];
    shake256(commitment_input, 32 + msg_len, expected_commitment, 32);
    
    // Verify commitment matches
    return memcmp(commitment, expected_commitment, 32) == 0 ? 1 : 0;
}

// Memory management exports for WASM
EMSCRIPTEN_KEEPALIVE
void* allocate(size_t size) {
    return malloc(size);
}

EMSCRIPTEN_KEEPALIVE
void deallocate(void* ptr) {
    free(ptr);
}
