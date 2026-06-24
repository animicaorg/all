/* animica_fastpow._fastpow — native SHA3-256 nonce scanner for Animica PoW.
 *
 * The PoW (matching python/animica/animica_cpu_miner_repoexact.py::digest_from_sign_bytes):
 *
 *     digest = SHA3-256( prefix || mix_seed || nonce.to_bytes(8, "little") )
 *     share is valid iff int.from_bytes(digest, "big") <= target
 *
 * scan() does that loop entirely in C with the GIL released, so a Python
 * ProcessPool/ThreadPool worker gets native hashrate instead of the ~KH/s the
 * pure-Python loop manages. The absorb of (prefix || mix_seed) is computed once
 * and cloned per nonce, so only the final 8-byte block + permutation runs in the
 * inner loop.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>
#include "sha3.h"

/* scan(prefix, mix_seed, target, start_nonce, iterations)
 *   prefix, mix_seed : bytes-like (mix_seed may be empty)
 *   target           : 32 bytes, big-endian
 *   start_nonce      : unsigned 64-bit
 *   iterations       : how many consecutive nonces to try (wraps at 2**64)
 * returns (nonce:int, digest:bytes[32]) on the first share, else None.
 */
static PyObject *fastpow_scan(PyObject *self, PyObject *args)
{
    Py_buffer prefix, mix_seed, target;
    unsigned long long start_nonce = 0, iterations = 0;

    if (!PyArg_ParseTuple(args, "y*y*y*KK",
                          &prefix, &mix_seed, &target,
                          &start_nonce, &iterations))
        return NULL;

    if (target.len != 32) {
        PyBuffer_Release(&prefix);
        PyBuffer_Release(&mix_seed);
        PyBuffer_Release(&target);
        PyErr_SetString(PyExc_ValueError, "target must be exactly 32 bytes (big-endian)");
        return NULL;
    }

    /* Absorb the invariant prefix once; clone this state for each nonce. */
    sha3_ctx_t base;
    sha3_init(&base, 32);
    sha3_update(&base, prefix.buf, (size_t) prefix.len);
    if (mix_seed.len > 0)
        sha3_update(&base, mix_seed.buf, (size_t) mix_seed.len);

    unsigned char target_be[32];
    memcpy(target_be, target.buf, 32);

    int found = 0;
    unsigned long long found_nonce = 0;
    unsigned char found_digest[32];
    unsigned long long nonce = start_nonce;

    Py_BEGIN_ALLOW_THREADS
    {
        unsigned char digest[32];
        unsigned char noncebuf[8];
        unsigned long long i;
        for (i = 0; i < iterations; i++) {
            /* nonce as 8 bytes little-endian */
            noncebuf[0] = (unsigned char)(nonce & 0xff);
            noncebuf[1] = (unsigned char)((nonce >> 8) & 0xff);
            noncebuf[2] = (unsigned char)((nonce >> 16) & 0xff);
            noncebuf[3] = (unsigned char)((nonce >> 24) & 0xff);
            noncebuf[4] = (unsigned char)((nonce >> 32) & 0xff);
            noncebuf[5] = (unsigned char)((nonce >> 40) & 0xff);
            noncebuf[6] = (unsigned char)((nonce >> 48) & 0xff);
            noncebuf[7] = (unsigned char)((nonce >> 56) & 0xff);

            sha3_ctx_t ctx = base;          /* clone absorbed prefix state */
            sha3_update(&ctx, noncebuf, 8);
            sha3_final(digest, &ctx);

            /* big-endian compare: memcmp <= 0  <=>  digest_int <= target_int */
            if (memcmp(digest, target_be, 32) <= 0) {
                found = 1;
                found_nonce = nonce;
                memcpy(found_digest, digest, 32);
                break;
            }
            nonce = (nonce + 1) & 0xFFFFFFFFFFFFFFFFULL;
        }
    }
    Py_END_ALLOW_THREADS

    PyBuffer_Release(&prefix);
    PyBuffer_Release(&mix_seed);
    PyBuffer_Release(&target);

    if (!found)
        Py_RETURN_NONE;
    return Py_BuildValue("(Ky#)", found_nonce, (const char *) found_digest, (Py_ssize_t) 32);
}

/* sha3_256(data) -> bytes[32]  — exposed for build-time correctness validation. */
static PyObject *fastpow_sha3_256(PyObject *self, PyObject *args)
{
    Py_buffer data;
    if (!PyArg_ParseTuple(args, "y*", &data))
        return NULL;
    unsigned char digest[32];
    sha3(data.buf, (size_t) data.len, digest, 32);
    PyBuffer_Release(&data);
    return Py_BuildValue("y#", (const char *) digest, (Py_ssize_t) 32);
}

static PyMethodDef FastpowMethods[] = {
    {"scan", fastpow_scan, METH_VARARGS,
     "scan(prefix, mix_seed, target, start_nonce, iterations) -> (nonce, digest) | None"},
    {"sha3_256", fastpow_sha3_256, METH_VARARGS,
     "sha3_256(data) -> bytes  (NIST SHA3-256, for validation)"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef fastpowmodule = {
    PyModuleDef_HEAD_INIT,
    "_fastpow",
    "Animica native SHA3-256 PoW nonce scanner.",
    -1,
    FastpowMethods
};

PyMODINIT_FUNC PyInit__fastpow(void)
{
    return PyModule_Create(&fastpowmodule);
}
