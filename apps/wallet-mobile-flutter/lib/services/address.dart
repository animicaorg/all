// Animica bech32m address encoding/decoding.
//
// Matches the canonical format used by the Python SDK (pq/py/address.py)
// and the Chrome wallet extension (apps/wallet-extension/src/lib/address):
//
//   address = bech32m("anim", u16be(alg_id) || sha3_256(pubkey))
//
// Payload is always 34 bytes: 2 bytes alg_id + 32 bytes digest.
// Contract addresses use alg_id = 0x0000.
//
// IMPORTANT: this is bech32m (BIP350, checksum constant 0x2bc830a3), NOT the
// original bech32 (constant 1). We implement it inline rather than using the
// pub.dev `bech32` package, which only supports bech32. That package produced
// addresses whose 6-character checksum differed from the chain's bech32m form,
// so the SAME wallet.json showed a different address on mobile than on desktop
// (which uses the Python backend). The data part was identical; only the
// checksum diverged. Keep this in lockstep with pq/py/address.py.

import 'dart:typed_data';
import 'package:pointycastle/digests/sha3.dart';

const String _hrp = 'anim';
const int _payloadLen = 34;
const String _charset = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const int _bech32mConst = 0x2bc830a3;
const List<int> _generator = <int>[
  0x3b6a57b2,
  0x26508e6d,
  0x1ea119fa,
  0x3d4233dd,
  0x2a1462b3,
];

Uint8List _sha3_256(Uint8List data) {
  final digest = SHA3Digest(256);
  digest.update(data, 0, data.length);
  final out = Uint8List(32);
  digest.doFinal(out, 0);
  return out;
}

int _polymod(List<int> values) {
  var chk = 1;
  for (final v in values) {
    final top = chk >> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (var i = 0; i < 5; i++) {
      if (((top >> i) & 1) == 1) {
        chk ^= _generator[i];
      }
    }
  }
  return chk;
}

List<int> _hrpExpand(String hrp) {
  final out = <int>[];
  for (final c in hrp.codeUnits) {
    out.add(c >> 5);
  }
  out.add(0);
  for (final c in hrp.codeUnits) {
    out.add(c & 31);
  }
  return out;
}

List<int> _createChecksum(String hrp, List<int> data) {
  final values = <int>[..._hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0];
  final pm = _polymod(values) ^ _bech32mConst;
  return <int>[for (var i = 0; i < 6; i++) (pm >> (5 * (5 - i))) & 31];
}

bool _verifyChecksum(String hrp, List<int> data) {
  return _polymod(<int>[..._hrpExpand(hrp), ...data]) == _bech32mConst;
}

String _bech32mEncode(String hrp, List<int> data) {
  final combined = <int>[...data, ..._createChecksum(hrp, data)];
  final sb = StringBuffer(hrp)..write('1');
  for (final d in combined) {
    sb.write(_charset[d]);
  }
  return sb.toString();
}

/// Decode a bech32m string into its 5-bit data words (checksum stripped).
/// Throws [FormatException] on a bad hrp, charset, or checksum.
List<int> _bech32mDecode(String addr) {
  if (addr != addr.toLowerCase() && addr != addr.toUpperCase()) {
    throw const FormatException('mixed-case bech32m string');
  }
  final s = addr.toLowerCase();
  final pos = s.lastIndexOf('1');
  if (pos < 1 || pos + 7 > s.length) {
    throw const FormatException('invalid bech32m separator position');
  }
  final hrp = s.substring(0, pos);
  final data = <int>[];
  for (final c in s.substring(pos + 1).codeUnits) {
    final idx = _charset.indexOf(String.fromCharCode(c));
    if (idx == -1) {
      throw const FormatException('invalid bech32m character');
    }
    data.add(idx);
  }
  if (!_verifyChecksum(hrp, data)) {
    throw const FormatException('invalid bech32m checksum');
  }
  return data.sublist(0, data.length - 6);
}

List<int> _toWords(Uint8List bytes) {
  // 8-bit-to-5-bit pad step per BIP173.
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
  return _bech32mEncode(_hrp, _toWords(payload));
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
  return _bech32mEncode(_hrp, _toWords(bytes));
}

class DecodedAddress {
  final int algId;
  final Uint8List digest;
  const DecodedAddress(this.algId, this.digest);
}

DecodedAddress decodeAddress(String addr) {
  final words = _bech32mDecode(addr);
  if (addr.toLowerCase().substring(0, addr.toLowerCase().lastIndexOf('1')) !=
      _hrp) {
    throw FormatException('address hrp must be "$_hrp"');
  }
  final payload = _fromWords(words);
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
