/*
 * Animica Wallet — Bech32/Bech32m codec
 *
 * Implements BIP-0173 (bech32) and BIP-0350 (bech32m) with a small API
 * specialized for Animica addresses (default HRP = "am", bech32m).
 *
 * This file provides:
 *  - Generic encode/decode for bech32 / bech32m
 *  - Animica helpers:
 *      AnimicaAddr.isValid(addr)
 *      AnimicaAddr.encodeFromBytes(bytes) -> "am1..."
 *      AnimicaAddr.decodeToBytes(addr) -> Uint8List?
 *  - 5-bit <-> 8-bit convertBits (for payload packing)
 *
 * Notes:
 *  - We accept lowercase or UPPERCASE but not mixed-case (per spec).
 *  - For Animica we use HRP "am" and **bech32m** variant by default.
 */

import 'dart:typed_data';

enum Bech32Variant { bech32, bech32m }

class Bech32Decoding {
  final String hrp;
  final List<int> data; // 5-bit words (without checksum)
  final Bech32Variant variant;
  const Bech32Decoding(this.hrp, this.data, this.variant);
}

class Bech32 {
  // Alphabet (32 chars)
  static const String _charset = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';

  static final Map<int, int> _charsetRev = () {
    final m = <int, int>{};
    for (var i = 0; i < _charset.length; i++) {
      m[_charset.codeUnitAt(i)] = i;
    }
    return m;
  }();

  // Checksum constants
  static const int _constBech32 = 1;
  static const int _constBech32m = 0x2bc830a3;

  // polymod generator
  static const List<int> _gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];

  static List<int> _hrpExpand(String hrp) {
    final out = <int>[];
    final hrpBytes = hrp.codeUnits;
    for (final c in hrpBytes) {
      out.add((c >> 5) & 0x07);
    }
    out.add(0);
    for (final c in hrpBytes) {
      out.add(c & 0x1f);
    }
    return out;
  }

  static int _polymod(List<int> values) {
    var chk = 1;
    for (final v in values) {
      final top = (chk >> 25) & 0xff;
      chk = ((chk & 0x1ffffff) << 5) ^ v;
      for (var i = 0; i < 5; i++) {
        if (((top >> i) & 1) != 0) {
          chk ^= _gen[i];
        }
      }
    }
    return chk;
  }

  static List<int> _createChecksum(String hrp, List<int> data, Bech32Variant variant) {
    final constv = (variant == Bech32Variant.bech32) ? _constBech32 : _constBech32m;
    final values = <int>[];
    values.addAll(_hrpExpand(hrp));
    values.addAll(data);
    values.addAll(List.filled(6, 0));
    final mod = _polymod(values) ^ constv;
    final ret = List<int>.filled(6, 0);
    for (var p = 0; p < 6; p++) {
      ret[p] = (mod >> (5 * (5 - p))) & 0x1f;
    }
    return ret;
  }

  static bool _verifyChecksum(String hrp, List<int> data) {
    final values = <int>[];
    values.addAll(_hrpExpand(hrp));
    values.addAll(data);
    final pm = _polymod(values);
    return pm == _constBech32 || pm == _constBech32m;
  }

  static Bech32Variant? _inferVariant(String hrp, List<int> data) {
    final values = <int>[];
    values.addAll(_hrpExpand(hrp));
    values.addAll(data);
    final pm = _polymod(values);
    if (pm == _constBech32) return Bech32Variant.bech32;
    if (pm == _constBech32m) return Bech32Variant.bech32m;
    return null;
  }

  /// Encode (hrp, data-5bit) to bech32/bech32m string.
  static String? encode(String hrp, List<int> data5, Bech32Variant variant) {
    if (!_checkHrp(hrp)) return null;
    if (!_checkData5(data5)) return null;

    final checksum = _createChecksum(hrp, data5, variant);
    final combined = [...data5, ...checksum];

    final sb = StringBuffer();
    sb.write(hrp);
    sb.write('1');
    for (final v in combined) {
      if (v < 0 || v > 31) return null;
      sb.write(_charset[v]);
    }
    final s = sb.toString();

    // Length checks per spec (overall length <= 90)
    if (s.length < 8 || s.length > 90) return null;
    return s;
  }

  /// Decode bech32/bech32m string -> (hrp, data-5bit, variant).
  static Bech32Decoding? decode(String input) {
    if (input.length < 8 || input.length > 90) return null;

    // Reject mixed case, and normalize to lowercase
    final hasLower = input.contains(RegExp(r'[a-z]'));
    final hasUpper = input.contains(RegExp(r'[A-Z]'));
    if (hasLower && hasUpper) return null;
    final s = input.toLowerCase();

    final pos = s.lastIndexOf('1');
    if (pos < 1 || pos + 7 > s.length) return null; // need at least HRP(1) + '1' + 6 checksum

    final hrp = s.substring(0, pos);
    final dataPart = s.substring(pos + 1);

    if (!_checkHrp(hrp)) return null;

    final data = <int>[];
    for (var i = 0; i < dataPart.length; i++) {
      final c = dataPart.codeUnitAt(i);
      final v = _charsetRev[c];
      if (v == null) return null;
      data.add(v);
    }
    if (data.length < 6) return null;
    if (!_verifyChecksum(hrp, data)) return null;

    final variant = _inferVariant(hrp, data);
    if (variant == null) return null;

    // strip checksum (last 6 symbols)
    final payload = data.sublist(0, data.length - 6);
    return Bech32Decoding(hrp, payload, variant);
  }

  static bool _checkHrp(String hrp) {
    if (hrp.isEmpty) return false;
    for (final c in hrp.codeUnits) {
      if (c < 33 || c > 126) return false;
    }
    return true;
  }

  static bool _checkData5(List<int> data) {
    for (final v in data) {
      if (v < 0 || v > 31) return false;
    }
    return true;
  }

  /// Convert bits (e.g., 8 -> 5 to pack bytes for bech32).
  /// Returns null if input contains values out of range or padding invalid.
  ///
  /// Based on BIP-0173 reference algorithm.
  static List<int>? convertBits(List<int> data, int from, int to, {bool pad = true}) {
    var acc = 0;
    var bits = 0;
    final maxv = (1 << to) - 1;
    final maxAcc = (1 << (from + to - 1)) - 1;

    final ret = <int>[];
    for (final value in data) {
      if (value < 0 || (value >> from) != 0) return null;
      acc = ((acc << from) | value) & maxAcc;
      bits += from;
      while (bits >= to) {
        bits -= to;
        ret.add((acc >> bits) & maxv);
      }
    }

    if (pad) {
      if (bits > 0) {
        ret.add((acc << (to - bits)) & maxv);
      }
    } else if (bits >= from || ((acc << (to - bits)) & maxv) != 0) {
      // leftover bits are not zero; invalid if no padding allowed
      return null;
    }
    return ret;
    }
}

/// Animica-specific helpers (HRP "am", bech32m by default).
class AnimicaAddr {
  static const String hrp = 'am';
  static const Bech32Variant variant = Bech32Variant.bech32m;

  /// Validate address structure + checksum + HRP + bech32m.
  static bool isValid(String address) {
    final dec = Bech32.decode(address);
    if (dec == null) return false;
    if (dec.variant != variant) return false;
    if (dec.hrp != hrp) return false;
    return dec.data.isNotEmpty; // non-empty payload
  }

  /// Encode raw payload bytes as "am1..." bech32m.
  /// Note: This does not enforce a particular version schema; it simply packs bytes.
  static String? encodeFromBytes(Uint8List bytes) {
    final data5 = Bech32.convertBits(bytes, 8, 5, pad: true);
    if (data5 == null) return null;
    return Bech32.encode(hrp, data5, variant);
  }

  /// Decode "am1..." into payload bytes.
  static Uint8List? decodeToBytes(String address) {
    final dec = Bech32.decode(address);
    if (dec == null || dec.hrp != hrp || dec.variant != variant) return null;
    final bytes = Bech32.convertBits(dec.data, 5, 8, pad: false);
    if (bytes == null) return null;
    return Uint8List.fromList(bytes);
  }

  /// Decode to data-5bit words (without checksum).
  static List<int>? decodeData5(String address) {
    final dec = Bech32.decode(address);
    if (dec == null || dec.hrp != hrp || dec.variant != variant) return null;
    return dec.data;
  }
}
