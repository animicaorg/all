/*
 * Animica Wallet — Keyring Facade
 *
 * Responsibilities:
 *  • Create/import a wallet (mnemonic) and persist to SecureStore
 *  • Lock/Unlock (cache mnemonic in memory on unlock; clear on lock)
 *  • Export mnemonic (biometric-gated by default)
 *  • Derive PQ keypairs (Dilithium3 / SPHINCS+) on demand
 *  • Sign/verify helpers via pluggable providers (dev or native)
 *  • Wipe all secrets
 *
 * Notes:
 *  • The mnemonic is the *only* root secret we persist by default.
 *    PQ secret keys are derived on-demand from the mnemonic seed.
 *  • For additional safety, pass biometric: true to read/write ops when available.
 */

import 'dart:async';
import 'dart:typed_data';

import 'mnemonic.dart' show Mnemonic;
import 'secure_store.dart' show secureStore, SecretKeys, SecureStore;
import 'key_derivation.dart' show KeyDerivation, PqKeyPair;
import 'pq_sign.dart' show PqSigner, PqAlgs, PqSignature;

/// Status of the keyring with respect to in-memory cache.
enum KeyringStatus { empty, locked, unlocked }

/// High-level errors.
class KeyringError implements Exception {
  final String message;
  KeyringError(this.message);
  @override
  String toString() => 'KeyringError: $message';
}

/// Public facade singleton (or construct your own for testing).
final keyring = Keyring();

class Keyring {
  final SecureStore _store;
  String? _cachedMnemonic;
  String _passphrase = ''; // Optional BIP39-like passphrase (NFKD stubbed).

  final StreamController<KeyringStatus> _statusCtrl =
      StreamController<KeyringStatus>.broadcast();

  Keyring({SecureStore? store}) : _store = store ?? secureStore;

  // ---------------- Status ----------------

  KeyringStatus get status {
    if (!(_initializedMnemonic ?? false)) return KeyringStatus.empty;
    return _cachedMnemonic == null ? KeyringStatus.locked : KeyringStatus.unlocked;
  }

  Stream<KeyringStatus> get statusStream => _statusCtrl.stream;

  void _emit() {
    if (!_statusCtrl.isClosed) _statusCtrl.add(status);
  }

  bool? _initializedMnemonic; // null until checked first time

  /// Returns true if a mnemonic exists in SecureStore.
  Future<bool> hasWallet() async {
    if (_initializedMnemonic != null) return _initializedMnemonic!;
    final exists = await _store.contains(SecretKeys.mnemonicV1);
    _initializedMnemonic = exists;
    _emit();
    return exists;
  }

  // ---------------- Create / Import ----------------

  /// Create a new wallet: generates a mnemonic and saves it (optionally behind biometrics).
  /// Returns the generated mnemonic (caller should display a backup flow).
  Future<String> createWallet({
    int strength = 256,
    bool biometric = false,
    String passphrase = '',
  }) async {
    if (await hasWallet()) {
      throw KeyringError('Wallet already exists. Wipe or import over it explicitly.');
    }
    final mnemonic = Mnemonic.generate(strength: strength);
    await _store.saveMnemonic(mnemonic, biometric: biometric);
    _cachedMnemonic = mnemonic;
    _passphrase = passphrase;
    _initializedMnemonic = true;
    _emit();
    return mnemonic;
  }

  /// Import an existing mnemonic (validated by checksum).
  Future<void> importWallet({
    required String mnemonic,
    bool biometric = false,
    String passphrase = '',
  }) async {
    // Validate: will throw if invalid checksum/words
    Mnemonic.mnemonicToEntropy(mnemonic);
    await _store.saveMnemonic(mnemonic, biometric: biometric);
    _cachedMnemonic = mnemonic;
    _passphrase = passphrase;
    _initializedMnemonic = true;
    _emit();
  }

  // ---------------- Locking ----------------

  /// Clear any in-memory secrets. Data remains in SecureStore.
  void lock() {
    _cachedMnemonic = null;
    _emit();
  }

  /// Load mnemonic into memory (optionally requiring biometric).
  /// Returns true if successful/unlocked.
  Future<bool> unlock({bool biometric = false, String passphrase = ''}) async {
    final exists = await hasWallet();
    if (!exists) return false;
    final m = await _store.loadMnemonic(biometric: biometric);
    if (m == null || m.isEmpty) return false;
    _cachedMnemonic = m;
    _passphrase = passphrase;
    _emit();
    return true;
  }

  // ---------------- Export / Wipe ----------------

  /// Export mnemonic (default: require biometric). Caller must secure UI.
  Future<String> exportMnemonic({bool biometric = true}) async {
    final m = await _store.loadMnemonic(biometric: biometric);
    if (m == null || m.isEmpty) {
      throw KeyringError('No mnemonic found (or biometric/auth failed).');
    }
    return m;
  }

  /// DANGER: Delete mnemonic and all secrets for this app namespace.
  Future<void> wipe() async {
    await _store.deleteAllInNamespace();
    _cachedMnemonic = null;
    _passphrase = '';
    _initializedMnemonic = false;
    _emit();
  }

  // ---------------- Derivation helpers ----------------

  /// Compute the PBKDF seed (64 bytes) from the mnemonic in memory.
  /// If locked, this will try to unlock using SecureStore (no biometric).
  Future<Uint8List> _seedOrThrow() async {
    if (!await hasWallet()) {
      throw KeyringError('No wallet initialized.');
    }
    final mnemonic = _cachedMnemonic ??
        (await _store.loadMnemonic(biometric: false)) ??
        (throw KeyringError('Wallet is locked; call unlock() first.'));
    return Mnemonic.mnemonicToSeed(mnemonic, passphrase: _passphrase);
  }

  /// Derive a Dilithium3 keypair for [account] using [signer] provider.
  Future<PqKeyPair> deriveDilithium3({
    required PqSigner signer,
    int account = 0,
  }) async {
    final ikm = await _seedOrThrow();
    final mat = KeyDerivation.dilithium3FromIkm(ikm: ikm, account: account, seedLen: 48);
    return signer.deriveKeypairFromMnemonic(
      PqAlgs.dilithium3,
      mnemonic: _cachedMnemonic!, // safe because _seedOrThrow ensured it
      passphrase: _passphrase,
      account: account,
    );
  }

  /// Derive a SPHINCS+ keypair for [account] using [signer] provider.
  Future<PqKeyPair> deriveSphincsPlus({
    required PqSigner signer,
    int account = 0,
  }) async {
    final ikm = await _seedOrThrow();
    final mat = KeyDerivation.sphincsPlusFromIkm(ikm: ikm, account: account, seedLen: 64);
    return signer.deriveKeypairFromMnemonic(
      PqAlgs.sphincsPlus,
      mnemonic: _cachedMnemonic!,
      passphrase: _passphrase,
      account: account,
    );
  }

  // ---------------- Sign / Verify ----------------

  /// Sign [message] using derived account key (Dilithium3).
  Future<PqSignature> signDilithium3({
    required PqSigner signer,
    required Uint8List message,
    int account = 0,
  }) async {
    final kp = await deriveDilithium3(signer: signer, account: account);
    return signer.sign(PqAlgs.dilithium3, kp.secretKey, message);
  }

  /// Verify Dilithium3 signature with a given public key.
  bool verifyDilithium3({
    required PqSigner signer,
    required Uint8List publicKey,
    required Uint8List message,
    required PqSignature signature,
  }) {
    return signer.verify(PqAlgs.dilithium3, publicKey, message, signature);
  }

  /// Sign [message] using derived account key (SPHINCS+).
  Future<PqSignature> signSphincsPlus({
    required PqSigner signer,
    required Uint8List message,
    int account = 0,
  }) async {
    final kp = await deriveSphincsPlus(signer: signer, account: account);
    return signer.sign(PqAlgs.sphincsPlus, kp.secretKey, message);
  }

  /// Verify SPHINCS+ signature with a given public key.
  bool verifySphincsPlus({
    required PqSigner signer,
    required Uint8List publicKey,
    required Uint8List message,
    required PqSignature signature,
  }) {
    return signer.verify(PqAlgs.sphincsPlus, publicKey, message, signature);
  }
}
