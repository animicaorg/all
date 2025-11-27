/*
 * Animica Wallet — Key Derivation (PQ stubs)
 *
 * Deterministic, domain-separated seed derivation for post-quantum keypairs:
 *  • Dilithium3 (signature)
 *  • SPHINCS+  (signature)
 *
 * This file DOES NOT implement the PQ crypto itself. Instead, it derives
 * high-entropy, deterministic seeds that you can feed into native libraries
 * (via FFI) to produce real keypairs. The goal is to keep derivation stable
 * and cross-platform while letting a vetted implementation handle the math.
 *
 * Sources/Notes (sizes are guidelines for seed material only):
 *  • Dilithium3: typical public key ≈ 1952 bytes, secret key ≈ 4000 bytes.
 *    Many libraries accept a 48–64 byte RNG seed to expand into a keypair.
 *  • SPHINCS+: parameter sets vary; libraries commonly accept a 64-byte seed.
 *
 * Derivation design:
 *  • Start from the wallet PBKDF seed (Mnemonic.mnemonicToSeed).
 *  • Use HKDF-SHA3-256 (extract+expand) with clear domain separation:
 *      salt = "animica/pq/dk/v1"
 *      info = "animica/pq/<alg>/account=<n>/purpose=sign/v1"
 *  • Return a PqKeyMaterial { algorithm, account, seed, path }.
 *
 * Plugging in native PQ:
 *  • Add an FFI bridge (see PqNative) that exposes:
 *      makeDilithium3Keypair(seed) -> (pk, sk)
 *      makeSphincsPlusKeypair(seed) -> (pk, sk)
 *    and call those from `expandToKeypair(...)`.
 */

import 'dart:convert' show utf8;
import 'dart:typed_data';

import 'mnemonic.dart' show Mnemonic;

/// Returned by derivation functions. `seed` is deterministic for a given
/// (ikm, algorithm, account, purpose, version).
class PqKeyMaterial {
  final String algorithm; // "dilithium3" | "sphincs+"
  final int account;
  final String path;      // e.g., m/pq/dilithium3/0
  final Uint8List seed;   // HKDF output (48 or 64 bytes by default)

  const PqKeyMaterial({
    required this.algorithm,
    required this.account,
    required this.path,
    required this.seed,
  });

  /// Placeholder: expand into a real keypair via native bindings.
  /// Replace with a proper FFI call.
  PqKeyPair expandToKeypair({required PqNativeBridge native}) {
    switch (algorithm) {
      case 'dilithium3':
        return native.makeDilithium3Keypair(seed);
      case 'sphincs+':
        return native.makeSphincsPlusKeypair(seed);
      default:
        throw UnsupportedError('Unknown PQ algorithm: $algorithm');
    }
  }
}

/// Simple holder for an expanded keypair. The exact sizes/formats are defined
/// by the native library. Keep as raw bytes; encode as needed (e.g., hex/base64).
class PqKeyPair {
  final Uint8List publicKey;
  final Uint8List secretKey;
  const PqKeyPair({required this.publicKey, required this.secretKey});
}

/// Stub interface for the native/FFI side. Implement this in a platform-
/// specific package (Android/iOS/macOS/Linux/Windows) and inject it.
abstract class PqNativeBridge {
  PqKeyPair makeDilithium3Keypair(Uint8List seed);
  PqKeyPair makeSphincsPlusKeypair(Uint8List seed);
}

class KeyDerivation {
  static const _salt = 'animica/pq/dk/v1';

  /// Derive Dilithium3 seed (default 48 bytes).
  static PqKeyMaterial dilithium3FromIkm({
    required Uint8List ikm,
    int account = 0,
    int seedLen = 48,
  }) {
    final info = _info(alg: 'dilithium3', account: account);
    final seed = Mnemonic.seedHkdfExpand(
      ikm: ikm,
      salt: Uint8List.fromList(utf8.encode(_salt)),
      info: info,
      length: seedLen,
    );
    return PqKeyMaterial(
      algorithm: 'dilithium3',
      account: account,
      path: 'm/pq/dilithium3/$account',
      seed: seed,
    );
  }

  /// Convenience: derive starting from a mnemonic.
  static PqKeyMaterial dilithium3FromMnemonic(
    String mnemonic, {
    String passphrase = '',
    int account = 0,
    int seedLen = 48,
  }) {
    final ikm = Mnemonic.mnemonicToSeed(mnemonic, passphrase: passphrase);
    return dilithium3FromIkm(ikm: ikm, account: account, seedLen: seedLen);
  }

  /// Derive SPHINCS+ seed (default 64 bytes).
  static PqKeyMaterial sphincsPlusFromIkm({
    required Uint8List ikm,
    int account = 0,
    int seedLen = 64,
  }) {
    final info = _info(alg: 'sphincs+', account: account);
    final seed = Mnemonic.seedHkdfExpand(
      ikm: ikm,
      salt: Uint8List.fromList(utf8.encode(_salt)),
      info: info,
      length: seedLen,
    );
    return PqKeyMaterial(
      algorithm: 'sphincs+',
      account: account,
      path: 'm/pq/sphincs+/$account',
      seed: seed,
    );
  }

  /// Convenience: derive starting from a mnemonic.
  static PqKeyMaterial sphincsPlusFromMnemonic(
    String mnemonic, {
    String passphrase = '',
    int account = 0,
    int seedLen = 64,
  }) {
    final ikm = Mnemonic.mnemonicToSeed(mnemonic, passphrase: passphrase);
    return sphincsPlusFromIkm(ikm: ikm, account: account, seedLen: seedLen);
  }

  /// Internal: build HKDF info = "animica/pq/<alg>/account=<n>/purpose=sign/v1"
  static List<int> _info({required String alg, required int account}) {
    final s = 'animica/pq/$alg/account=$account/purpose=sign/v1';
    return utf8.encode(s);
  }
}
