/* tiny_sha3 — public-domain SHA-3 / Keccak, by Markku-Juhani O. Saarinen.
 * NIST SHA3 (0x06 domain separation). Used here for Animica's SHA3-256 PoW.
 * Little-endian hosts only (x86/ARM miners) — the keccak state is byte-addressed.
 */
#ifndef ANIMICA_SHA3_H
#define ANIMICA_SHA3_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    union {
        uint8_t b[200];   /* state, byte view  */
        uint64_t q[25];   /* state, lane view  */
    } st;
    int pt, rsiz, mdlen;
} sha3_ctx_t;

int sha3_init(sha3_ctx_t *c, int mdlen);                 /* mdlen = 32 for SHA3-256 */
int sha3_update(sha3_ctx_t *c, const void *data, size_t len);
int sha3_final(void *md, sha3_ctx_t *c);
void *sha3(const void *in, size_t inlen, void *md, int mdlen);

#endif /* ANIMICA_SHA3_H */
