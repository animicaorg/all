// Secure-storage backed account vault.
//
// On iOS this uses Keychain; on Android, EncryptedSharedPreferences.
// Inside that, the JSON blob is additionally AES-GCM encrypted with a
// key derived from the user's unlock password (see services/auth.dart).
//
// Storage layout:
//   "animica.wallet.accounts.v1" → encryptToString(vaultJson)
//
// If `unlockKey` is null we fall through to plaintext mode for the
// transition where a user hasn't set a password yet — but Riverpod's
// startup flow gates the UI so this path is only hit during the
// first-run setup screen.

import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../models/account.dart';
import 'auth.dart';

const _kAccountsKey = 'animica.wallet.accounts.v1';

class Vault {
  final FlutterSecureStorage _storage;
  Uint8List? unlockKey;

  Vault({FlutterSecureStorage? storage, this.unlockKey})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(
                accessibility: KeychainAccessibility.unlocked_this_device,
              ),
            );

  Future<List<Account>> load() async {
    final raw = await _storage.read(key: _kAccountsKey);
    if (raw == null) return const [];
    if (unlockKey == null) {
      // Legacy / first-run path — try to parse as plaintext.
      return decodeVault(raw);
    }
    final decrypted = decryptFromString(unlockKey!, raw);
    if (decrypted == null) {
      throw StateError('Vault decrypt failed — wrong password?');
    }
    return decodeVault(decrypted);
  }

  Future<void> save(List<Account> accounts) async {
    final plaintext = encodeVault(accounts);
    if (unlockKey == null) {
      await _storage.write(key: _kAccountsKey, value: plaintext);
      return;
    }
    final encrypted = encryptToString(unlockKey!, plaintext);
    await _storage.write(key: _kAccountsKey, value: encrypted);
  }

  Future<void> add(Account account) async {
    final existing = await load();
    final filtered = existing.where((a) => a.address != account.address).toList()
      ..add(account);
    await save(filtered);
  }

  Future<void> remove(String address) async {
    final filtered = (await load())
        .where((a) => a.address != address)
        .toList(growable: false);
    await save(filtered);
  }

  Future<void> wipe() => _storage.delete(key: _kAccountsKey);
}
