// Animica Dilithium3 — Dart port of the chain's `dilithium_py/dilithium3.py`
// reference impl. Pure SHAKE-256, no lattice math.
//
// The chain's verifier (animica._vendor.dilithium_py.Dilithium3.verify)
// only checks the 32-byte commitment at the start of the signature:
//
//   commitment = SHAKE-256(pk[:32] || msg, 32)
//
// The remaining 3261 bytes of the 3293-byte signature are deterministic
// SHAKE-256 padding that exists only to keep the bytes stable across
// rebroadcasts of the same tx (mempool idempotency). They're not used
// for verification.
//
// Reference: python/animica/_vendor/dilithium_py/dilithium3.py
// All hex constants here are the exact byte strings used by the Python
// reference (`b"dilithium3_pk|"`, `b"dilithium3_sk|"`, etc.) so the two
// impls are byte-for-byte interchangeable. Asserted by the golden-vector
// test in test/dilithium3_test.dart.

import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha3.dart';

import '../constants.dart';

const int _pkBytes = 1952;
const int _skBytes = 4000;
const int _sigBytes = 3293;

class Dilithium3Keypair {
  final Uint8List publicKey;
  final Uint8List secretKey;
  const Dilithium3Keypair(this.publicKey, this.secretKey);
}

// ── SHAKE-256 helper ────────────────────────────────────────────────────
//
// pointycastle's SHA3Digest with bits=256 in SHAKE mode acts as SHAKE-256.
// We wrap it so callers can ask for arbitrary output lengths.

Uint8List _shake256(List<Uint8List> chunks, int outBytes) {
  final d = SHA3Digest(256, true); // true = SHAKE mode
  for (final c in chunks) {
    d.update(c, 0, c.length);
  }
  final out = Uint8List(outBytes);
  d.doOutput(out, 0, outBytes);
  return out;
}

Uint8List _bytes(String s) => Uint8List.fromList(s.codeUnits);

Uint8List _concat(List<Uint8List> parts) {
  final n = parts.fold<int>(0, (a, b) => a + b.length);
  final out = Uint8List(n);
  var off = 0;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

// ── Public API ─────────────────────────────────────────────────────────

/// Generate a Dilithium3 keypair, deterministic if `seed` (32B) is given.
///
///   sk = seed || SHAKE-256("dilithium3_sk|" || seed, 3968)
///   pk = SHAKE-256("dilithium3_pk|" || seed, 1952)
Dilithium3Keypair generateDilithium3Keypair([Uint8List? seed]) {
  final s = seed ?? _randomBytes(32);
  if (s.length != 32) {
    throw ArgumentError('Dilithium3 seed must be 32 bytes');
  }
  final pk = _shake256([_bytes('dilithium3_pk|'), s], _pkBytes);
  final skRest = _shake256([_bytes('dilithium3_sk|'), s], _skBytes - 32);
  final sk = _concat([s, skRest]);
  assert(sk.length == _skBytes);
  assert(pk.length == _pkBytes);
  return Dilithium3Keypair(pk, sk);
}

/// Sign a 64-byte prehash with Dilithium3.
///
/// `prehash` is whatever `services/canonical.dart:buildSignBytes` produced
/// (typically the SHA3-512 of the canonical sign-bytes).
///
/// `pk` MUST be the public key that matches `sk` — the reference impl
/// re-derives pk from sk[:32] when it isn't passed, but our Account model
/// always carries both so we pass pk through for parity with imported
/// wallets that may have been generated outside this library.
///
/// `seed` is optional explicit signing randomness (32B). When omitted,
/// we use the hedged scheme: `rng_seed = SHAKE-256("dilithium3_rng|" ||
/// sk[:32] || SHAKE-256(prehash, 64), 32)`. Hedged signatures are
/// deterministic for the same (sk, prehash) pair.
Uint8List signDilithium3({
  required Uint8List sk,
  required Uint8List prehash,
  required Uint8List pk,
  Uint8List? seed,
}) {
  if (sk.length != _skBytes) {
    throw ArgumentError('Dilithium3 secret key must be $_skBytes bytes');
  }
  if (pk.length != _pkBytes) {
    throw ArgumentError('Dilithium3 public key must be $_pkBytes bytes');
  }

  // msg_hash = SHAKE-256(prehash, 64)
  final msgHash = _shake256([prehash], 64);

  // rng_seed (hedged or explicit)
  Uint8List rngSeed;
  if (seed == null) {
    final rngInput = _concat([sk.sublist(0, 32), msgHash]);
    rngSeed = _shake256([_bytes('dilithium3_rng|'), rngInput], 32);
  } else {
    if (seed.length != 32) {
      throw ArgumentError('Dilithium3 sign seed must be 32 bytes');
    }
    rngSeed = seed;
  }

  // commitment = SHAKE-256(pk[:32] || prehash, 32)
  final commitment =
      _shake256([pk.sublist(0, 32), prehash], 32);

  // sig_rest = SHAKE-256("dilithium3_sig|" || commitment || sk[:32]
  //                      || msg_hash || rng_seed,
  //                      sig_bytes - 32)
  final sigInput = _concat([
    commitment,
    sk.sublist(0, 32),
    msgHash,
    rngSeed,
  ]);
  final sigRest =
      _shake256([_bytes('dilithium3_sig|'), sigInput], _sigBytes - 32);

  return _concat([commitment, sigRest]);
}

/// Verify a Dilithium3 signature exactly the way the chain does.
/// Provided for completeness — wallets normally don't verify their own
/// outbound sigs.
bool verifyDilithium3({
  required Uint8List pk,
  required Uint8List prehash,
  required Uint8List sig,
}) {
  if (pk.length != _pkBytes) return false;
  if (sig.length != _sigBytes) return false;
  final commitment = sig.sublist(0, 32);
  final expected = _shake256([pk.sublist(0, 32), prehash], 32);
  for (var i = 0; i < 32; i++) {
    if (commitment[i] != expected[i]) return false;
  }
  return true;
}

Uint8List _randomBytes(int n) {
  final r = Random.secure();
  return Uint8List.fromList(List<int>.generate(n, (_) => r.nextInt(256)));
}

// ── alg constants exported for callers ─────────────────────────────────

int get dilithium3AlgId => AnimicaConfig.algIdDilithium3;
int get dilithium3PubkeyLen => _pkBytes;
int get dilithium3SecretLen => _skBytes;
int get dilithium3SigLen => _sigBytes;
