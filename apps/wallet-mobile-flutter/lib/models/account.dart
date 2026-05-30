// Wallet account model + JSON round-trip for secure storage.

import 'dart:convert';
import 'dart:typed_data';

import '../constants.dart';
import '../services/address.dart';

class Account {
  final String label;
  final int algId;
  final Uint8List publicKey;
  final Uint8List secretKey;
  final String createdAt;

  Account({
    required this.label,
    required this.algId,
    required this.publicKey,
    required this.secretKey,
    String? createdAt,
  }) : createdAt = createdAt ?? DateTime.now().toUtc().toIso8601String();

  String get address => addressFromPubkey(publicKey, algId);

  String get algName {
    switch (algId) {
      case AnimicaConfig.algIdSphincs:
        return 'sphincs_shake_128s';
      case AnimicaConfig.algIdDilithium3:
        return 'dilithium3';
      default:
        return 'unknown';
    }
  }

  Map<String, dynamic> toJson() => {
        'label': label,
        'algId': algId,
        'publicKeyHex': _hex(publicKey),
        'secretKeyHex': _hex(secretKey),
        'createdAt': createdAt,
      };

  static Account fromJson(Map<String, dynamic> j) => Account(
        label: j['label'] as String,
        algId: j['algId'] as int,
        publicKey: _bytes(j['publicKeyHex'] as String),
        secretKey: _bytes(j['secretKeyHex'] as String),
        createdAt: j['createdAt'] as String?,
      );

  static String _hex(Uint8List b) =>
      b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

  static Uint8List _bytes(String hex) {
    var clean = hex;
    if (clean.startsWith('0x') || clean.startsWith('0X')) {
      clean = clean.substring(2);
    }
    final out = Uint8List(clean.length ~/ 2);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(clean.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return out;
  }
}

/// JSON-encode a list of accounts as a single string for secure-storage.
String encodeVault(List<Account> accounts) =>
    jsonEncode(accounts.map((a) => a.toJson()).toList());

List<Account> decodeVault(String? raw) {
  if (raw == null || raw.isEmpty) return const [];
  final parsed = jsonDecode(raw);
  if (parsed is! List) return const [];
  return parsed
      .whereType<Map<String, dynamic>>()
      .map(Account.fromJson)
      .toList(growable: false);
}
