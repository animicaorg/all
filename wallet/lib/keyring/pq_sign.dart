/*
 * Animica Wallet — PQ sign/verify (pure Dart, pluggable native)
 *
 * This module provides a pluggable interface for post-quantum (PQ) signatures
 * with two providers:
 *
 *  1) DevInsecureSignProvider (DEFAULT for development)
 *     - Pure Dart, NO REAL CRYPTO. It creates a deterministic "signature"
 *       = HMAC-SHA3-256(publicKey, prehash(message)).
 *     - Public key is deterministically derived from a seed so that verify()
 *       can work using only the public key.
 *     - For UI/integration testing only (flows, storage, serialization).
 *
 *  2) NativeBridgeSignProvider (stub)
 *     - Wraps a native FFI bridge for Dilithium3 / SPHINCS+ real signatures.
 *     - You must implement sign/verify in your native layer; this class will
 *       throw until those methods are provided.
 *
 * Key generation seeds come from KeyDerivation (HKDF-SHA3 over PBKDF seed).
 */

import 'dart:convert' show utf8;
import 'dart:typed_data';

import '../crypto/sha3.dart' as sha3;
import 'key_derivation.dart'
    show PqKeyMaterial, PqKeyPair, KeyDerivation, PqNativeBridge;

/// Supported algorithm string constants (kept as strings to match configs).
class PqAlgs {
  static const String dilithium3 = 'dilithium3';
  static const String sphincsPlus = 'sphincs+';
}

/// A detached signature.
class PqSignature {
  final String algorithm; // e.g., 'dilithium3' | 'sphincs+'
  final Uint8List bytes;  // provider-defined encoding; dev = 32 bytes
  const PqSignature({required this.algorithm, required this.bytes});
}

/// Generic interface for a signing provider.
abstract class PqSignProvider {
  /// Produce a keypair from deterministic seed material.
  PqKeyPair generateKeypair(String algorithm, Uint8List seed);

  /// Create a detached signature over [message] using [secretKey].
  Uint8List sign(String algorithm, Uint8List secretKey, Uint8List message);

  /// Verify [signature] over [message] using [publicKey].
  bool verify(String algorithm, Uint8List publicKey, Uint8List message, Uint8List signature);
}

/// ----------------------------------------------------------------------------
/// Dev provider (INSECURE). For tests & UI plumbing.
/// ----------------------------------------------------------------------------
/// Construction:
///   pk = keccak256("pub:<alg>:v1" || seed)
///   sig = HMAC-SHA3-256( key = pk, msg = prehash(message) )
///
/// Verification recomputes `sig` using only the public key.
/// This is NOT secure and MUST NOT be used in production.
/// ----------------------------------------------------------------------------
class DevInsecureSignProvider implements PqSignProvider {
  static const _domainSig = 'animica.sig.v1';

  @override
  PqKeyPair generateKeypair(String algorithm, Uint8List seed) {
    final pk = _pubFromSeed(algorithm, seed);
    // "Secret key" is the seed itself for determinism in dev.
    return PqKeyPair(publicKey: pk, secretKey: Uint8List.fromList(seed));
  }

  @override
  Uint8List sign(String algorithm, Uint8List secretKey, Uint8List message) {
    final pk = _pubFromSeed(algorithm, secretKey);
    final ph = _prehash(message);
    return _hmacSha3_256(pk, ph); // 32 bytes
  }

  @override
  bool verify(String algorithm, Uint8List publicKey, Uint8List message, Uint8List signature) {
    final ph = _prehash(message);
    final want = _hmacSha3_256(publicKey, ph);
    return _eq(signature, want);
  }

  Uint8List _pubFromSeed(String alg, Uint8List seed) {
    final label = utf8.encode('pub:$alg:v1');
    return sha3.keccak256(Uint8List.fromList([...label, ...seed]));
  }

  Uint8List _prehash(Uint8List msg) {
    final d = utf8.encode(_domainSig);
    return sha3.keccak256(Uint8List.fromList([...d, ...msg]));
  }

  // HMAC-SHA3-256 with 136-byte rate block size
  Uint8List _hmacSha3_256(Uint8List key, Uint8List msg) {
    const blockSize = 136;
    var k = key;
    if (k.length > blockSize) k = sha3.sha3_256(k);
    if (k.length < blockSize) {
      final kk = Uint8List(blockSize)..setAll(0, k);
      k = kk;
    }
    final o = Uint8List(blockSize), i = Uint8List(blockSize);
    for (var n = 0; n < blockSize; n++) {
      o[n] = 0x5c ^ k[n];
      i[n] = 0x36 ^ k[n];
    }
    final inner = sha3.sha3_256(Uint8List.fromList([...i, ...msg]));
    final outer = sha3.sha3_256(Uint8List.fromList([...o, ...inner]));
    return outer;
  }

  bool _eq(Uint8List a, Uint8List b) {
    if (a.length != b.length) return false;
    var v = 0;
    for (var i = 0; i < a.length; i++) {
      v |= a[i] ^ b[i];
    }
    return v == 0;
  }
}

/// ----------------------------------------------------------------------------
/// Native bridge provider (real crypto via FFI) — STUB
/// ----------------------------------------------------------------------------
/// Your platform plugin should extend [PqNativeBridge] to expose:
///   - makeDilithium3Keypair(seed) -> PqKeyPair
///   - makeSphincsPlusKeypair(seed) -> PqKeyPair
/// and ALSO provide sign/verify. Until then, sign/verify will throw.
/// ----------------------------------------------------------------------------
abstract class PqNativeSignBridge extends PqNativeBridge {
  Uint8List dilithium3_sign(Uint8List secretKey, Uint8List message);
  bool dilithium3_verify(Uint8List publicKey, Uint8List message, Uint8List signature);

  Uint8List sphincsplus_sign(Uint8List secretKey, Uint8List message);
  bool sphincsplus_verify(Uint8List publicKey, Uint8List message, Uint8List signature);
}

class NativeBridgeSignProvider implements PqSignProvider {
  final PqNativeSignBridge native;
  NativeBridgeSignProvider(this.native);

  @override
  PqKeyPair generateKeypair(String algorithm, Uint8List seed) {
    switch (algorithm) {
      case PqAlgs.dilithium3:
        return native.makeDilithium3Keypair(seed);
      case PqAlgs.sphincsPlus:
        return native.makeSphincsPlusKeypair(seed);
      default:
        throw UnsupportedError('Unknown PQ algorithm: $algorithm');
    }
  }

  @override
  Uint8List sign(String algorithm, Uint8List secretKey, Uint8List message) {
    switch (algorithm) {
      case PqAlgs.dilithium3:
        return native.dilithium3_sign(secretKey, message);
      case PqAlgs.sphincsPlus:
        return native.sphincsplus_sign(secretKey, message);
      default:
        throw UnsupportedError('Unknown PQ algorithm: $algorithm');
    }
  }

  @override
  bool verify(String algorithm, Uint8List publicKey, Uint8List message, Uint8List signature) {
    switch (algorithm) {
      case PqAlgs.dilithium3:
        return native.dilithium3_verify(publicKey, message, signature);
      case PqAlgs.sphincsPlus:
        return native.sphincsplus_verify(publicKey, message, signature);
      default:
        throw UnsupportedError('Unknown PQ algorithm: $algorithm');
    }
  }
}

/// ----------------------------------------------------------------------------
/// Facade helpers
/// ----------------------------------------------------------------------------
class PqSigner {
  final PqSignProvider _provider;
  const PqSigner(this._provider);

  /// Create an insecure dev signer (pure Dart). For tests only.
  factory PqSigner.dev() => PqSigner(DevInsecureSignProvider());

  /// Create a signer backed by your native bridge.
  factory PqSigner.native(PqNativeSignBridge bridge) =>
      PqSigner(NativeBridgeSignProvider(bridge));

  /// Derive a deterministic keypair from a mnemonic for [algorithm].
  ///   - Dilithium3 uses 48-byte seed by default
  ///   - SPHINCS+   uses 64-byte seed by default
  PqKeyPair deriveKeypairFromMnemonic(
    String algorithm, {
    required String mnemonic,
    String passphrase = '',
    int account = 0,
  }) {
    PqKeyMaterial mat;
    switch (algorithm) {
      case PqAlgs.dilithium3:
        mat = KeyDerivation.dilithium3FromMnemonic(
          mnemonic,
          passphrase: passphrase,
          account: account,
          seedLen: 48,
        );
        break;
      case PqAlgs.sphincsPlus:
        mat = KeyDerivation.sphincsPlusFromMnemonic(
          mnemonic,
          passphrase: passphrase,
          account: account,
          seedLen: 64,
        );
        break;
      default:
        throw UnsupportedError('Unknown PQ algorithm: $algorithm');
    }
    return _provider.generateKeypair(algorithm, mat.seed);
  }

  /// Sign a message (bytes). Returns a PqSignature wrapper.
  PqSignature sign(String algorithm, Uint8List secretKey, Uint8List message) {
    final sig = _provider.sign(algorithm, secretKey, message);
    return PqSignature(algorithm: algorithm, bytes: sig);
  }

  /// Verify a detached signature over [message].
  bool verify(String algorithm, Uint8List publicKey, Uint8List message, PqSignature signature) {
    if (signature.algorithm != algorithm) return false;
    return _provider.verify(algorithm, publicKey, message, signature.bytes);
  }
}
