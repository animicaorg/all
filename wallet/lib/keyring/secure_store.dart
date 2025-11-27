/*
 * Animica Wallet — SecureStore
 *
 * Thin wrapper around flutter_secure_storage providing:
 *  • Namespaced keys ("animica.wallet.v1/<key>")
 *  • Byte-friendly read/write (Base64 encoding)
 *  • Optional biometric requirement (when supported)
 *  • Cross-platform default options (iOS/macOS/Android/Web/Desktop)
 *
 * Notes:
 *  • This module avoids logging secret values.
 *  • Biometric gating support depends on flutter_secure_storage version /
 *    platform capabilities. Options below are set conservatively and won't
 *    crash if a flag is unsupported (it will be ignored by the platform).
 *
 * Common keys (suggested):
 *  • SecretKeys.mnemonicV1                → utf8 string
 *  • SecretKeys.dilithium3Sk(int account) → raw private key/seed bytes
 *  • SecretKeys.sphincsPlusSk(int account)→ raw private key/seed bytes
 */

import 'dart:async';
import 'dart:convert' show base64, utf8;
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Canonical key names used throughout the wallet.
class SecretKeys {
  static const String mnemonicV1 = 'mnemonic_v1';

  static String dilithium3Sk(int account) => 'pq/dilithium3/sk/$account';
  static String dilithium3Pk(int account) => 'pq/dilithium3/pk/$account';

  static String sphincsPlusSk(int account) => 'pq/sphincs+/sk/$account';
  static String sphincsPlusPk(int account) => 'pq/sphincs+/pk/$account';

  /// Example for a symmetric key (e.g., local encryption).
  static const String localSymmetricKey = 'sym/local/v1';
}

/// SecureStore wrapper (singleton friendly; you can also DI this).
class SecureStore {
  final String namespace;
  final FlutterSecureStorage _storage;

  SecureStore({
    this.namespace = 'animica.wallet.v1',
    FlutterSecureStorage? storage,
  }) : _storage = storage ?? const FlutterSecureStorage();

  /// Namespacing to avoid collisions with other apps or prior versions.
  String _ns(String key) => '$namespace/$key';

  // ---------------- Platform options ----------------

  AndroidOptions _android({bool requireAuth = false}) {
    // Flags are best-effort; unsupported ones are ignored.
    return const AndroidOptions(
      encryptedSharedPreferences: true,
      resetOnError: true,
      sharedPreferencesName: 'animica_secure_prefs',
      preferencesKeyPrefix: 'anm_',
      // For newer plugin versions, you can enable biometric reauth per-read:
      // biometricAuthentication: requireAuth,
      // authenticationValidityDurationSeconds: 0, // force every time
    );
  }

  IOSOptions _ios({bool requireAuth = false}) {
    return IOSOptions(
      accountName: 'animica.wallet',
      accessibility: KeychainAccessibility.first_unlock, // usable after first unlock
      // Some plugin versions support:
      // authenticationRequired: requireAuth,
    );
  }

  MacOsOptions _macos({bool requireAuth = false}) {
    return MacOsOptions(
      accessibility: KeychainAccessibility.first_unlock,
      // authenticationRequired: requireAuth,
    );
  }

  LinuxOptions _linux() {
    // Uses SecretService if available.
    return const LinuxOptions();
  }

  WindowsOptions _windows() {
    return const WindowsOptions();
  }

  WebOptions _web() {
    // IndexedDB (default). Avoids synchronous storage.
    return const WebOptions(
      dbName: 'animica_secure',
      publicKey: 'animica_web_kek', // only affects older web impls
    );
  }

  // ---------------- Bytes helpers ----------------

  Future<void> writeBytes(
    String key,
    Uint8List value, {
    bool biometric = false,
  }) async {
    final nsKey = _ns(key);
    final v = base64.encode(value);
    await _storage.write(
      key: nsKey,
      value: v,
      aOptions: _android(requireAuth: biometric),
      iOptions: _ios(requireAuth: biometric),
      mOptions: _macos(requireAuth: biometric),
      lOptions: _linux(),
      wOptions: _windows(),
      webOptions: _web(),
    );
  }

  Future<Uint8List?> readBytes(
    String key, {
    bool biometric = false,
  }) async {
    final nsKey = _ns(key);
    final v = await _storage.read(
      key: nsKey,
      aOptions: _android(requireAuth: biometric),
      iOptions: _ios(requireAuth: biometric),
      mOptions: _macos(requireAuth: biometric),
      lOptions: _linux(),
      wOptions: _windows(),
      webOptions: _web(),
    );
    if (v == null) return null;
    try {
      return Uint8List.fromList(base64.decode(v));
    } catch (_) {
      // If legacy plain string somehow exists, return utf8 bytes.
      return Uint8List.fromList(utf8.encode(v));
    }
  }

  Future<void> writeString(
    String key,
    String value, {
    bool biometric = false,
  }) async {
    await writeBytes(key, Uint8List.fromList(utf8.encode(value)), biometric: biometric);
  }

  Future<String?> readString(
    String key, {
    bool biometric = false,
  }) async {
    final b = await readBytes(key, biometric: biometric);
    return b == null ? null : utf8.decode(b);
  }

  Future<bool> contains(String key) async {
    final nsKey = _ns(key);
    return _storage.containsKey(
      key: nsKey,
      aOptions: _android(),
      iOptions: _ios(),
      mOptions: _macos(),
      lOptions: _linux(),
      wOptions: _windows(),
      webOptions: _web(),
    );
  }

  /// Delete a single key.
  Future<void> delete(String key) async {
    final nsKey = _ns(key);
    await _storage.delete(
      key: nsKey,
      aOptions: _android(),
      iOptions: _ios(),
      mOptions: _macos(),
      lOptions: _linux(),
      wOptions: _windows(),
      webOptions: _web(),
    );
  }

  /// Delete all keys in this namespace only.
  Future<void> deleteAllInNamespace() async {
    final all = await readAll();
    for (final k in all.keys) {
      await _storage.delete(
        key: k,
        aOptions: _android(),
        iOptions: _ios(),
        mOptions: _macos(),
        lOptions: _linux(),
        wOptions: _windows(),
        webOptions: _web(),
      );
    }
  }

  /// Read all keys (namespaced) → values as strings (Base64 or plain).
  /// WARNING: Do not log or export this unless explicitly requested.
  Future<Map<String, String>> readAll() async {
    final raw = await _storage.readAll(
      aOptions: _android(),
      iOptions: _ios(),
      mOptions: _macos(),
      lOptions: _linux(),
      wOptions: _windows(),
      webOptions: _web(),
    );
    final nsPrefix = '$namespace/';
    final out = <String, String>{};
    raw.forEach((k, v) {
      if (k.startsWith(nsPrefix)) {
        out[k] = v;
      }
    });
    return out;
  }

  // ---------------- Convenience secrets ----------------

  Future<void> saveMnemonic(String phrase, {bool biometric = false}) =>
      writeString(SecretKeys.mnemonicV1, phrase, biometric: biometric);

  Future<String?> loadMnemonic({bool biometric = false}) =>
      readString(SecretKeys.mnemonicV1, biometric: biometric);

  Future<void> deleteMnemonic() => delete(SecretKeys.mnemonicV1);

  Future<void> saveDilithium3Sk(int account, Uint8List sk, {bool biometric = false}) =>
      writeBytes(SecretKeys.dilithium3Sk(account), sk, biometric: biometric);

  Future<Uint8List?> loadDilithium3Sk(int account, {bool biometric = false}) =>
      readBytes(SecretKeys.dilithium3Sk(account), biometric: biometric);

  Future<void> saveSphincsPlusSk(int account, Uint8List sk, {bool biometric = false}) =>
      writeBytes(SecretKeys.sphincsPlusSk(account), sk, biometric: biometric);

  Future<Uint8List?> loadSphincsPlusSk(int account, {bool biometric = false}) =>
      readBytes(SecretKeys.sphincsPlusSk(account), biometric: biometric);
}

/// A default, app-wide store instance you can import where DI is overkill.
final secureStore = SecureStore();
