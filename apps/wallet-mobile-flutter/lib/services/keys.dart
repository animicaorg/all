// PQ key generation + signing for Animica.
//
// Two schemes are exposed, both byte-for-byte compatible with the chain's
// reference impls in `python/animica/_vendor/`:
//
//   - SPHINCS-SHAKE-128s (alg_id 0x1002).
//       keygen: sk = random(64)
//               pk = SHA3-512("pk" || u64be(len(sk)) || sk)   → 64 bytes
//       sign : sig = SHAKE-256("sig" || u64be(len(pk)) || pk
//                              || u64be(len(prehash)) || prehash, 7856)
//
//   - Dilithium3 / ML-DSA-65 (alg_id 0x1001). Implemented in
//     `dilithium3.dart` — see that file's header for the formula.
//     Despite the name, the chain's reference is a commitment-style stub
//     (175 lines of pure SHAKE-256), not the real lattice ML-DSA-65, so
//     this is a clean Dart port rather than a port of NTT + sampling.

import 'dart:math';
import 'dart:typed_data';
import 'package:pointycastle/digests/sha3.dart';
import 'package:pointycastle/digests/shake.dart';

import '../constants.dart';

// Re-export the legacy Dilithium3 stub for callers that import keys.dart.
// (Pure Dart, broken commitment-stub kept for receive-side compat.)
export 'dilithium3.dart'
    show generateDilithium3Keypair, signDilithium3, verifyDilithium3, Dilithium3Keypair;

// ── byte helpers ────────────────────────────────────────────────────────

Uint8List _u64be(int n) {
  final out = Uint8List(8);
  var v = BigInt.from(n);
  for (var i = 7; i >= 0; i--) {
    out[i] = (v & BigInt.from(0xff)).toInt();
    v = v >> 8;
  }
  return out;
}

Uint8List _concat(List<Uint8List> parts) {
  final total = parts.fold<int>(0, (acc, p) => acc + p.length);
  final out = Uint8List(total);
  var off = 0;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

Uint8List _sha3_512(Uint8List input) {
  final d = SHA3Digest(512);
  d.update(input, 0, input.length);
  final out = Uint8List(64);
  d.doFinal(out, 0);
  return out;
}

Uint8List _shake256(Uint8List input, int outLen) {
  final d = SHAKEDigest(256);
  d.update(input, 0, input.length);
  final out = Uint8List(outLen);
  d.doOutput(out, 0, outLen);
  return out;
}

// ── SPHINCS-128s ────────────────────────────────────────────────────────

class SphincsKeypair {
  final Uint8List publicKey;
  final Uint8List secretKey;
  const SphincsKeypair(this.publicKey, this.secretKey);
}

/// Generate a fresh SPHINCS-SHAKE-128s keypair (Animica variant).
SphincsKeypair generateSphincsKeypair() {
  final rnd = Random.secure();
  final sk = Uint8List.fromList(
    List<int>.generate(AnimicaConfig.sphincsSecretLen, (_) => rnd.nextInt(256)),
  );
  return SphincsKeypair(derivePublicKeyFromSecret(sk), sk);
}

/// Derive the public key from a SPHINCS-128s secret key.
///
///   pk = SHA3-512("pk" || u64be(len(sk)) || sk)    // truncated to 64
///
/// Implementation parity with python `pq.py.algs.pure_python_fallbacks._h`
/// for out_len=64 (which equals the SHA3-512 output size, so no XOF
/// expansion is needed).
Uint8List derivePublicKeyFromSecret(Uint8List sk) {
  if (sk.length != AnimicaConfig.sphincsSecretLen) {
    throw ArgumentError(
        'sphincs secret key must be ${AnimicaConfig.sphincsSecretLen} bytes');
  }
  final tag = Uint8List.fromList('pk'.codeUnits);
  final input = _concat([tag, _u64be(sk.length), sk]);
  return _sha3_512(input);
}

/// Produce a SPHINCS-SHAKE-128s signature compatible with Animica's
/// pure-Python fallback verifier.
///
///   sig = SHAKE-256("sig" || u64be(len(pk)) || pk
///                  || u64be(len(prehash)) || prehash, 7856)
///
/// `prehash` is the 64-byte canonical sign-bytes digest produced by
/// `signer.dart:buildSignBytes`.
Uint8List signSphincs(Uint8List pk, Uint8List prehash) {
  if (pk.length != AnimicaConfig.sphincsPubkeyLen) {
    throw ArgumentError(
        'sphincs pubkey must be ${AnimicaConfig.sphincsPubkeyLen} bytes');
  }
  final tag = Uint8List.fromList('sig'.codeUnits);
  final input = _concat([tag, _u64be(pk.length), pk, _u64be(prehash.length), prehash]);
  return _shake256(input, AnimicaConfig.sphincsSigLen);
}

