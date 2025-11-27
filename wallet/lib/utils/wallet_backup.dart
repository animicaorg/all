import 'dart:convert';
import 'dart:typed_data';

/// Simple representation of an Animica wallet backup file.
///
/// Format (v1):
/// {
///   "version": 1,
///   "mnemonic": "...",
///   "passphrase": "", // optional
///   "exportedAt": "2024-01-01T00:00:00.000Z"
/// }
class WalletBackupFile {
  static const currentVersion = 1;

  final int version;
  final String mnemonic;
  final String passphrase;
  final DateTime exportedAt;

  const WalletBackupFile({
    required this.version,
    required this.mnemonic,
    this.passphrase = '',
    required this.exportedAt,
  });

  Map<String, dynamic> toJson() => {
        'version': version,
        'mnemonic': mnemonic,
        'passphrase': passphrase,
        'exportedAt': exportedAt.toIso8601String(),
      };

  Uint8List toBytes() => Uint8List.fromList(utf8.encode(jsonEncode(toJson())));

  factory WalletBackupFile.create({
    required String mnemonic,
    String passphrase = '',
  }) {
    return WalletBackupFile(
      version: currentVersion,
      mnemonic: mnemonic,
      passphrase: passphrase,
      exportedAt: DateTime.now().toUtc(),
    );
  }

  factory WalletBackupFile.fromJson(Map<String, dynamic> json) {
    final version = int.tryParse((json['version'] ?? '1').toString()) ?? 1;
    final mnemonic = (json['mnemonic'] ?? '').toString().trim();
    final passphrase = (json['passphrase'] ?? '').toString();
    final exportedAtRaw = (json['exportedAt'] ?? '').toString();
    final exportedAt = DateTime.tryParse(exportedAtRaw)?.toUtc() ??
        DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    if (mnemonic.isEmpty) {
      throw FormatException('Wallet backup missing mnemonic');
    }
    return WalletBackupFile(
      version: version,
      mnemonic: mnemonic,
      passphrase: passphrase,
      exportedAt: exportedAt,
    );
  }

  static WalletBackupFile parse(String data) {
    final decoded = jsonDecode(data);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Wallet backup must be a JSON object');
    }
    return WalletBackupFile.fromJson(decoded);
  }
}
