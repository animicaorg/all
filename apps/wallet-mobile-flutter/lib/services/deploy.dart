// Helpers for `animica_deployContract`: parsing the dapp-supplied byte
// payloads and deriving the address the deployed contract will land at.
//
// Kept out of signer.dart because both concerns are deploy-specific and
// neither belongs on the transfer/call signing path.

import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha3.dart';

import 'address.dart';

/// A `0x`-prefixed hex byte string. The prefix is matched case-INSENSITIVELY:
/// a `0X…` payload used to miss this regex and fall through to the UTF-8
/// branch below, which encoded the literal string `"0XDEADBEEF"` as contract
/// code and deployed 10 bytes of ASCII instead of 4 bytes of bytecode.
final RegExp _hexBytes = RegExp(r'^0[xX][0-9a-fA-F]*$');

/// Anything that LOOKS like it was meant to be hex. Used to tell a malformed
/// hex string (odd length, stray non-hex char) apart from a genuine plain-text
/// payload, so the former is an error instead of being silently UTF-8 encoded.
final RegExp _hexish = RegExp(r'^0[xX]');

/// Coerce a dapp-supplied `code` / `manifest` / `data` value to bytes.
///
/// Mirrors `parseBytesData` + `coerceBytes` in
/// apps/wallet-extension/src/background/index.ts so the same dapp payload
/// produces the same bytes on mobile and on desktop:
///   - `0x`/`0X`-prefixed even-length hex → those bytes
///   - list of 0..255 ints                → those bytes
///   - `{"0":1,"1":2,…}` numeric-key map  → those bytes (see below)
///   - `""` / `"0x"` / empty list         → empty bytes
///   - any other string                   → its UTF-8 bytes (plain-text
///                                          manifests)
///
/// Returns null when the value is absent, is a type we refuse to guess at
/// (numbers, bools, non-index maps), or is malformed hex — the caller turns
/// that into an InvalidParams reject.
///
/// The numeric-key map case is NOT hypothetical. The page passes its request
/// through `JSON.stringify` before it reaches the Flutter handler
/// (screens/browser.dart `_buildProviderScript`), and `JSON.stringify` turns a
/// `Uint8Array` into an OBJECT — `{"0":1,"1":2,…}` — not an array. A dapp that
/// hands `window.animica.request` a real `Uint8Array` for `code` (the natural
/// thing to do, and what the extension accepts) therefore arrived here as a
/// `Map` and was rejected as "Deploy params missing `code`". Decoding it
/// requires the keys to be exactly the contiguous indices `0..n-1` with byte
/// values, which is what a stringified typed array always is; anything looser
/// would let an arbitrary object be reinterpreted as bytecode.
Uint8List? parseDeployBytes(dynamic value) {
  if (value == null) return null;
  if (value is Uint8List) return value;
  if (value is List) {
    final out = Uint8List(value.length);
    for (var i = 0; i < value.length; i++) {
      final v = value[i];
      if (v is! int || v < 0 || v > 255) return null;
      out[i] = v;
    }
    return out;
  }
  if (value is Map) {
    return _bytesFromIndexMap(value);
  }
  if (value is String) {
    final trimmed = value.trim();
    if (trimmed.isEmpty || trimmed.toLowerCase() == '0x') {
      return Uint8List(0);
    }
    if (_hexish.hasMatch(trimmed)) {
      // Committed to the hex interpretation: a malformed body is an error,
      // never a UTF-8 fallback.
      if (!_hexBytes.hasMatch(trimmed) || !trimmed.length.isEven) return null;
      final hex = trimmed.substring(2);
      final out = Uint8List(hex.length ~/ 2);
      for (var i = 0; i < out.length; i++) {
        out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
      }
      return out;
    }
    // Not hex — treat as a literal payload (e.g. a JSON manifest pasted as
    // text). Encode the ORIGINAL string, not the trimmed one, so the bytes
    // match what the dapp will hash on its side.
    return Uint8List.fromList(utf8.encode(value));
  }
  return null;
}

/// `{"0":1,"1":2,…}` → bytes, or null when it is not a contiguous 0..n-1
/// index map of 0..255 values.
Uint8List? _bytesFromIndexMap(Map<dynamic, dynamic> map) {
  if (map.isEmpty) return Uint8List(0);
  final out = Uint8List(map.length);
  final seen = List<bool>.filled(map.length, false);
  for (final entry in map.entries) {
    final k = entry.key;
    final int? idx = k is int ? k : (k is String ? int.tryParse(k) : null);
    // Require the canonical decimal spelling ("7", never " 7" or "07"), which
    // is what JSON.stringify emits for a typed array.
    if (idx == null || idx.toString() != k.toString()) return null;
    if (idx < 0 || idx >= map.length || seen[idx]) return null;
    final v = entry.value;
    if (v is! int || v < 0 || v > 255) return null;
    seen[idx] = true;
    out[idx] = v;
  }
  return out;
}

const String _deployAddressDomainV1 = 'animica/deploy/python_vm_package/v1';

/// Address a python-vm package deploy will be created at, as `0x…` hex.
///
/// Byte-for-byte the same derivation the node publishes on the receipt
/// (`core/utils/deploy_metadata.derive_python_vm_package_address`, nonce
/// variant):
///
///   sha3_256(DOMAIN_V1 || u128be(chainId) || sender[-32:].rjust(32)
///            || 0x01 || u128be(nonce) || sha3_256(code) || sha3_256(manifest))
///
/// Computing it locally lets the launcher UI use the address immediately
/// instead of polling for the receipt. It is deterministic in the tx's own
/// fields, so it is a derivation and not a guess — but it only holds if the
/// tx is mined with this exact nonce.
String deriveDeployedContractAddress({
  required String from,
  required int chainId,
  required int nonce,
  required Uint8List code,
  required Uint8List manifest,
}) {
  final sender = decodeAddress(from).digest;
  final buf = BytesBuilder()
    ..add(utf8.encode(_deployAddressDomainV1))
    ..add(_u128be(chainId))
    ..add(_padLeft32(sender))
    ..addByte(0x01) // tag: nonce-derived (vs 0x02 validAfter/validUntil/salt)
    ..add(_u128be(nonce))
    ..add(sha3_256(code))
    ..add(sha3_256(manifest));
  return '0x${_hex(sha3_256(buf.toBytes()))}';
}

Uint8List sha3_256(Uint8List input) {
  final d = SHA3Digest(256);
  d.update(input, 0, input.length);
  final out = Uint8List(32);
  d.doFinal(out, 0);
  return out;
}

Uint8List _u128be(int value) {
  var n = value < 0 ? 0 : value;
  final out = Uint8List(16);
  for (var i = 15; i >= 0 && n > 0; i--) {
    out[i] = n & 0xff;
    n >>= 8;
  }
  return out;
}

Uint8List _padLeft32(Uint8List b) {
  if (b.length == 32) return b;
  final out = Uint8List(32);
  if (b.length > 32) {
    out.setRange(0, 32, b.sublist(b.length - 32));
  } else {
    out.setRange(32 - b.length, 32, b);
  }
  return out;
}

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
