// Animica bech32m address encoding/decoding.
//
// Matches the canonical format used by the Python SDK (pq/py/address.py)
// and the Chrome wallet extension (apps/wallet-extension/src/lib/address):
//
//   address = bech32m("anim", u16be(alg_id) || sha3_256(pubkey))
//
// Payload is always 34 bytes: 2 bytes alg_id + 32 bytes digest.
// Contract addresses use alg_id = 0x0000.

import 'dart:typed_data';
import 'package:bech32/bech32.dart';
import 'package:pointycastle/digests/sha3.dart';

const String _hrp = 'anim';
const int _payloadLen = 34;

Uint8List _sha3_256(Uint8List data) {
  final digest = SHA3Digest(256);
  digest.update(data, 0, data.length);
  final out = Uint8List(32);
  digest.doFinal(out, 0);
  return out;
}

List<int> _toWords(Uint8List bytes) {
  // bech32 lib's `Bech32` uses 5-bit "data" already; we need the
  // 8-bit-to-5-bit pad step. Adapted from BIP173 reference impl.
  var acc = 0;
  var bits = 0;
  final out = <int>[];
  for (final b in bytes) {
    acc = (acc << 8) | b;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      out.add((acc >> bits) & 0x1f);
    }
  }
  if (bits > 0) {
    out.add((acc << (5 - bits)) & 0x1f);
  }
  return out;
}

Uint8List _fromWords(List<int> words) {
  var acc = 0;
  var bits = 0;
  final out = <int>[];
  for (final w in words) {
    acc = (acc << 5) | w;
    bits += 5;
    while (bits >= 8) {
      bits -= 8;
      out.add((acc >> bits) & 0xff);
    }
  }
  return Uint8List.fromList(out);
}

/// Build an Animica address from a public key + algorithm id.
String addressFromPubkey(Uint8List pubkey, int algId) {
  final digest = _sha3_256(pubkey);
  final payload = Uint8List(_payloadLen)
    ..[0] = (algId >> 8) & 0xff
    ..[1] = algId & 0xff
    ..setRange(2, _payloadLen, digest);
  final words = _toWords(payload);
  final codec = Bech32Codec();
  return codec.encode(Bech32(_hrp, words), 1023);
}

/// 0x-hex 32-byte contract address → `anim1…` bech32m form
/// (uses alg_id=0x0000 for contracts).
String hexToBech32m(String hex) {
  var clean = hex;
  if (clean.startsWith('0x') || clean.startsWith('0X')) {
    clean = clean.substring(2);
  }
  if (clean.length != 64 || !RegExp(r'^[0-9a-fA-F]+$').hasMatch(clean)) {
    throw ArgumentError('hexToBech32m: expected 32-byte hex, got "$hex"');
  }
  final bytes = Uint8List(_payloadLen);
  for (var i = 0; i < 32; i++) {
    bytes[2 + i] = int.parse(clean.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return Bech32Codec().encode(Bech32(_hrp, _toWords(bytes)), 1023);
}

class DecodedAddress {
  final int algId;
  final Uint8List digest;
  const DecodedAddress(this.algId, this.digest);
}

DecodedAddress decodeAddress(String addr) {
  final b = Bech32Codec().decode(addr, 1023);
  if (b.hrp != _hrp) {
    throw FormatException(
        'address hrp must be "$_hrp", got "${b.hrp}"');
  }
  final payload = _fromWords(b.data);
  if (payload.length != _payloadLen) {
    throw FormatException(
        'address payload must be $_payloadLen bytes, got ${payload.length}');
  }
  final algId = (payload[0] << 8) | payload[1];
  final digest = Uint8List.fromList(payload.sublist(2));
  return DecodedAddress(algId, digest);
}

bool isValidAnimAddress(String s) {
  try {
    decodeAddress(s);
    return true;
  } catch (_) {
    return false;
  }
}
