/*
 * Animica Wallet — SHA3 / Keccak wrappers (hashlib-based)
 *
 * Provides:
 *   - sha3_256(bytes)    → Uint8List (32 bytes)
 *   - sha3_512(bytes)    → Uint8List (64 bytes)
 *   - keccak256(bytes)   → Uint8List (32 bytes, legacy pre-SHA3 padding)
 *   - hex helpers for the above
 *   - sha256(bytes)      → Uint8List (via package:crypto) — convenience
 */

import 'dart:convert' show utf8;
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as crypto show sha256;
import 'package:hashlib/hashlib.dart' as hashlib;

Uint8List sha3_256(List<int> data) =>
    Uint8List.fromList(hashlib.sha3_256.convert(data).bytes);

Uint8List sha3_512(List<int> data) =>
    Uint8List.fromList(hashlib.sha3_512.convert(data).bytes);

Uint8List keccak256(List<int> data) =>
    Uint8List.fromList(hashlib.keccak256.convert(data).bytes);

String hexSha3_256(List<int> data, {bool with0x = true}) =>
    _toHex(sha3_256(data), with0x: with0x);

String hexKeccak256(List<int> data, {bool with0x = true}) =>
    _toHex(keccak256(data), with0x: with0x);

/// Convenience: SHA-256 via package:crypto (sometimes handy for checksums)
Uint8List sha256(List<int> data) =>
    Uint8List.fromList(crypto.sha256.convert(data).bytes);

// Convenience helpers ---------------------------------------------------------

Uint8List sha3_256Utf8(String s) => sha3_256(utf8.encode(s));
Uint8List sha3_512Utf8(String s) => sha3_512(utf8.encode(s));
Uint8List keccak256Utf8(String s) => keccak256(utf8.encode(s));
String hexSha3_256Utf8(String s, {bool with0x = true}) =>
    hexSha3_256(utf8.encode(s), with0x: with0x);
String hexKeccak256Utf8(String s, {bool with0x = true}) =>
    hexKeccak256(utf8.encode(s), with0x: with0x);

String _toHex(List<int> bytes, {bool with0x = true}) {
  final sb = StringBuffer();
  if (with0x) sb.write('0x');
  const hexd = '0123456789abcdef';
  for (final b in bytes) {
    sb.write(hexd[(b >> 4) & 0xF]);
    sb.write(hexd[b & 0xF]);
  }
  return sb.toString();
}
