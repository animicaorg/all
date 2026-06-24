/* tiny_sha3 — public-domain SHA-3 / Keccak implementation.
 * Markku-Juhani O. Saarinen <mjos@iki.fi>, 2011-2015. Public domain.
 * Verified at build time against Python's hashlib.sha3_256 (NIST SHA3-256).
 */
#include "sha3.h"

#define ROTL64(x, y) (((x) << (y)) | ((x) >> (64 - (y))))

static const uint64_t keccakf_rndc[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};
static const int keccakf_rotc[24] = {
    1,  3,  6,  10, 15, 21, 28, 36, 45, 55, 2,  14,
    27, 41, 56, 8,  25, 43, 62, 18, 39, 61, 20, 44
};
static const int keccakf_piln[24] = {
    10, 7,  11, 17, 18, 3, 5,  16, 8,  21, 24, 4,
    15, 23, 19, 13, 12, 2, 20, 14, 22, 9,  6,  1
};

static void sha3_keccakf(uint64_t st[25])
{
    int i, j, r;
    uint64_t t, bc0, bc1, bc2, bc3, bc4;

    for (r = 0; r < 24; r++) {
        /* Theta — unrolled, no % 5 in the hot path */
        bc0 = st[0] ^ st[5] ^ st[10] ^ st[15] ^ st[20];
        bc1 = st[1] ^ st[6] ^ st[11] ^ st[16] ^ st[21];
        bc2 = st[2] ^ st[7] ^ st[12] ^ st[17] ^ st[22];
        bc3 = st[3] ^ st[8] ^ st[13] ^ st[18] ^ st[23];
        bc4 = st[4] ^ st[9] ^ st[14] ^ st[19] ^ st[24];

        t = bc4 ^ ROTL64(bc1, 1);
        st[0] ^= t; st[5] ^= t; st[10] ^= t; st[15] ^= t; st[20] ^= t;
        t = bc0 ^ ROTL64(bc2, 1);
        st[1] ^= t; st[6] ^= t; st[11] ^= t; st[16] ^= t; st[21] ^= t;
        t = bc1 ^ ROTL64(bc3, 1);
        st[2] ^= t; st[7] ^= t; st[12] ^= t; st[17] ^= t; st[22] ^= t;
        t = bc2 ^ ROTL64(bc4, 1);
        st[3] ^= t; st[8] ^= t; st[13] ^= t; st[18] ^= t; st[23] ^= t;
        t = bc3 ^ ROTL64(bc0, 1);
        st[4] ^= t; st[9] ^= t; st[14] ^= t; st[19] ^= t; st[24] ^= t;

        /* Rho Pi — table-driven (sequential dependency on t) */
        t = st[1];
        for (i = 0; i < 24; i++) {
            j = keccakf_piln[i];
            bc0 = st[j];
            st[j] = ROTL64(t, keccakf_rotc[i]);
            t = bc0;
        }

        /* Chi — unrolled per row, no % 5 */
        for (j = 0; j < 25; j += 5) {
            bc0 = st[j]; bc1 = st[j + 1]; bc2 = st[j + 2];
            bc3 = st[j + 3]; bc4 = st[j + 4];
            st[j]     ^= (~bc1) & bc2;
            st[j + 1] ^= (~bc2) & bc3;
            st[j + 2] ^= (~bc3) & bc4;
            st[j + 3] ^= (~bc4) & bc0;
            st[j + 4] ^= (~bc0) & bc1;
        }

        /* Iota */
        st[0] ^= keccakf_rndc[r];
    }
}

int sha3_init(sha3_ctx_t *c, int mdlen)
{
    int i;
    for (i = 0; i < 25; i++)
        c->st.q[i] = 0;
    c->mdlen = mdlen;
    c->rsiz = 200 - 2 * mdlen;
    c->pt = 0;
    return 1;
}

int sha3_update(sha3_ctx_t *c, const void *data, size_t len)
{
    size_t i;
    int j = c->pt;
    for (i = 0; i < len; i++) {
        c->st.b[j++] ^= ((const uint8_t *) data)[i];
        if (j >= c->rsiz) {
            sha3_keccakf(c->st.q);
            j = 0;
        }
    }
    c->pt = j;
    return 1;
}

int sha3_final(void *md, sha3_ctx_t *c)
{
    int i;
    c->st.b[c->pt] ^= 0x06;          /* NIST SHA3 domain separation */
    c->st.b[c->rsiz - 1] ^= 0x80;
    sha3_keccakf(c->st.q);
    for (i = 0; i < c->mdlen; i++)
        ((uint8_t *) md)[i] = c->st.b[i];
    return 1;
}

void *sha3(const void *in, size_t inlen, void *md, int mdlen)
{
    sha3_ctx_t c;
    sha3_init(&c, mdlen);
    sha3_update(&c, in, inlen);
    sha3_final(md, &c);
    return md;
}
