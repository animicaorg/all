// Import / export wallets.json — interop with the Animica CLI.
//
// The CLI stores wallets at `~/.animica/wallets.json` in this shape:
//
//   {
//     "wallets": [
//       {
//         "label": "main",
//         "address": "anim1…",
//         "alg_id": 4098,
//         "alg_name": "sphincs_shake_128s",
//         "public_key_hex": "…",
//         "pub_fingerprint": "…",
//         "created_at": "2026-04-06T15:31:46.779822+00:00",
//         "secret_key_hex": "…",
//         "pending_txs": []
//       }
//     ]
//   }
//
// Export produces the same shape; import accepts both the wrapping
// `{wallets: [...]}` form and a bare list.

import 'dart:convert';
import 'dart:typed_data';

import '../constants.dart';
import '../models/account.dart';
import 'auth.dart';

class ImportResult {
  final int imported;
  final int skipped;
  final List<String> errors;
  const ImportResult(
      {required this.imported, required this.skipped, required this.errors});
}

String exportWalletsJson(List<Account> accounts) {
  final out = {
    'wallets': accounts.map((a) => _accountToCli(a)).toList(),
  };
  return const JsonEncoder.withIndent('  ').convert(out);
}

Map<String, dynamic> _accountToCli(Account a) {
  final pkHex = _hex(a.publicKey);
  final fp = sha256Hex(a.publicKey).substring(0, 16);
  return {
    'label': a.label,
    'address': a.address,
    'alg_id': a.algId,
    'alg_name': a.algName,
    'public_key_hex': pkHex,
    'pub_fingerprint': fp,
    'created_at': a.createdAt,
    'secret_key_hex': _hex(a.secretKey),
    'pending_txs': const <dynamic>[],
  };
}

ImportResult importWalletsJson(String jsonText) {
  late final dynamic parsed;
  try {
    parsed = jsonDecode(jsonText);
  } catch (e) {
    return ImportResult(imported: 0, skipped: 0, errors: ['bad json: $e']);
  }

  List<dynamic> rawList;
  if (parsed is Map<String, dynamic> && parsed['wallets'] is List) {
    rawList = parsed['wallets'] as List;
  } else if (parsed is List) {
    rawList = parsed;
  } else {
    return const ImportResult(
        imported: 0, skipped: 0, errors: ['unexpected json shape']);
  }

  final imported = <Account>[];
  final errors = <String>[];
  var skipped = 0;

  for (final raw in rawList) {
    if (raw is! Map<String, dynamic>) {
      skipped++;
      continue;
    }
    try {
      imported.add(_accountFromCli(raw));
    } catch (e) {
      errors.add('${raw['label'] ?? '?'}: $e');
      skipped++;
    }
  }
  return ImportResult(
      imported: imported.length, skipped: skipped, errors: errors)
    .._accounts = imported;
}

// Stash the produced accounts on the result so callers can use them
// without re-running the import. (Two-phase API kept simple for v0.1.)
extension IRA on ImportResult {
  // ignore: library_private_types_in_public_api
  set _accounts(List<Account> v) => _accountsBuffer = v;
  List<Account> get accounts => _accountsBuffer;
}

List<Account> _accountsBuffer = const [];

Account _accountFromCli(Map<String, dynamic> j) {
  final algId = j['alg_id'] as int? ??
      (j['algId'] is int ? j['algId'] as int : AnimicaConfig.algIdMlDsa65);
  final pkHex = (j['public_key_hex'] ?? j['publicKeyHex']) as String?;
  final skHex = (j['secret_key_hex'] ?? j['secretKeyHex']) as String?;
  if (pkHex == null || skHex == null) {
    throw FormatException('wallet record missing public_key_hex or secret_key_hex');
  }
  return Account(
    label: j['label'] as String? ?? 'imported',
    algId: algId,
    publicKey: _bytes(pkHex),
    secretKey: _bytes(skHex),
    createdAt: j['created_at'] as String? ?? j['createdAt'] as String?,
  );
}

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

Uint8List _bytes(String hex) {
  var clean = hex;
  if (clean.startsWith('0x') || clean.startsWith('0X')) clean = clean.substring(2);
  final out = Uint8List(clean.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(clean.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}
