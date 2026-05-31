// Password / passcode service.
//
// Threat model: a stolen phone with the wallet installed. flutter_secure_storage
// already gates the raw blob behind Keychain / EncryptedSharedPreferences, but
// that protects against off-device extraction, not against someone who has the
// phone unlocked. Add a second layer here so the secret keys are AES-GCM
// encrypted with a key derived from a user passcode.
//
// Storage layout:
//   secure_storage:
//     "animica.wallet.auth.v1"    → JSON {salt, kdfRounds, verifierCt, verifierIv}
//     "animica.wallet.accounts.v1" → AES-GCM ciphertext of the vault JSON
//                                    (or plaintext for legacy / no-password mode)
//
// Verifier ciphertext lets us check "is this password correct?" without
// decrypting the vault — we encrypt a fixed plaintext under the derived
// key and store both the ciphertext and the IV; on unlock we attempt to
// decrypt and compare to the known plaintext.

import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as crypto;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:pointycastle/block/aes.dart';
import 'package:pointycastle/block/modes/gcm.dart';
import 'package:pointycastle/api.dart';
import 'package:pointycastle/key_derivators/pbkdf2.dart';
import 'package:pointycastle/key_derivators/api.dart';
import 'package:pointycastle/macs/hmac.dart';
import 'package:pointycastle/digests/sha256.dart';

const _kAuthKey = 'animica.wallet.auth.v1';
const _verifierPlain = 'animica.wallet.unlock-check.v1';
const _kdfRoundsDefault = 200000;
const _saltLen = 16;
const _ivLen = 12;

class AuthService {
  final FlutterSecureStorage _storage;
  AuthService({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(
                accessibility: KeychainAccessibility.unlocked_this_device,
              ),
            );

  Future<bool> isConfigured() async =>
      (await _storage.read(key: _kAuthKey)) != null;

  /// Set or replace the unlock password. Stores a fresh salt + verifier.
  Future<Uint8List> setPassword(String password) async {
    final salt = _randomBytes(_saltLen);
    final key = _deriveKey(password, salt, _kdfRoundsDefault);
    final iv = _randomBytes(_ivLen);
    final ct = _aesGcmEncrypt(key, iv, Uint8List.fromList(utf8.encode(_verifierPlain)));
    final record = {
      'salt': _hex(salt),
      'kdfRounds': _kdfRoundsDefault,
      'verifierCt': _hex(ct),
      'verifierIv': _hex(iv),
    };
    await _storage.write(key: _kAuthKey, value: jsonEncode(record));
    return key;
  }

  /// Verify a password; returns the derived encryption key on success,
  /// or null on failure.
  Future<Uint8List?> verify(String password) async {
    final raw = await _storage.read(key: _kAuthKey);
    if (raw == null) return null;
    final j = jsonDecode(raw) as Map<String, dynamic>;
    final salt = _bytes(j['salt'] as String);
    final rounds = j['kdfRounds'] as int;
    final ctExpect = _bytes(j['verifierCt'] as String);
    final iv = _bytes(j['verifierIv'] as String);
    final key = _deriveKey(password, salt, rounds);
    try {
      final pt = _aesGcmDecrypt(key, iv, ctExpect);
      if (utf8.decode(pt) == _verifierPlain) return key;
      return null;
    } catch (_) {
      return null;
    }
  }

  /// Permanently wipe stored auth + vault. Caller decides UX (export first?).
  Future<void> wipeAll() async {
    await _storage.deleteAll();
  }

  // ── primitives ──────────────────────────────────────────────────────

  Uint8List _deriveKey(String password, Uint8List salt, int rounds) {
    final pbkdf2 = PBKDF2KeyDerivator(HMac(SHA256Digest(), 64))
      ..init(Pbkdf2Parameters(salt, rounds, 32));
    return pbkdf2.process(Uint8List.fromList(utf8.encode(password)));
  }

  Uint8List _aesGcmEncrypt(Uint8List key, Uint8List iv, Uint8List plaintext) {
    final cipher = GCMBlockCipher(AESEngine())
      ..init(true, AEADParameters(KeyParameter(key), 128, iv, Uint8List(0)));
    return cipher.process(plaintext);
  }

  Uint8List _aesGcmDecrypt(Uint8List key, Uint8List iv, Uint8List ciphertext) {
    final cipher = GCMBlockCipher(AESEngine())
      ..init(false, AEADParameters(KeyParameter(key), 128, iv, Uint8List(0)));
    return cipher.process(ciphertext);
  }

  Uint8List _randomBytes(int n) {
    final r = Random.secure();
    return Uint8List.fromList(List<int>.generate(n, (_) => r.nextInt(256)));
  }

  String _hex(Uint8List b) =>
      b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

  Uint8List _bytes(String hex) {
    final out = Uint8List(hex.length ~/ 2);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return out;
  }
}

/// Public utility — encrypt/decrypt an arbitrary string blob with a key.
/// Used by Vault. Output format: u8be(ivLen) || iv || ciphertext, all hex.
String encryptToString(Uint8List key, String plaintext) {
  final auth = AuthService();
  final iv = auth._randomBytes(_ivLen);
  final ct = auth._aesGcmEncrypt(
      key, iv, Uint8List.fromList(utf8.encode(plaintext)));
  return jsonEncode({
    'v': 1,
    'iv': auth._hex(iv),
    'ct': auth._hex(ct),
  });
}

String? decryptFromString(Uint8List key, String? raw) {
  if (raw == null || raw.isEmpty) return null;
  try {
    final j = jsonDecode(raw) as Map<String, dynamic>;
    if (j['v'] != 1) return null;
    final auth = AuthService();
    final iv = auth._bytes(j['iv'] as String);
    final ct = auth._bytes(j['ct'] as String);
    final pt = auth._aesGcmDecrypt(key, iv, ct);
    return utf8.decode(pt);
  } catch (_) {
    return null;
  }
}

/// Hash a string with SHA-256 (used for pub_fingerprint when exporting in
/// CLI-compatible format).
String sha256Hex(Uint8List input) {
  return crypto.sha256.convert(input).bytes
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join();
}
