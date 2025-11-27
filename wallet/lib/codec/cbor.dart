/*
 * Animica Wallet — Deterministic CBOR (RFC 8949 §4.2.1)
 *
 * Features:
 *  • Deterministic (canonical) encoding:
 *      - Definite lengths only (no indefinite-length emission)
 *      - Minimal-length integer encodings
 *      - Map entries sorted by the lexicographic order of their *encoded keys*
 *  • Supported types for encode/decode:
 *      - int (±2^63..2^63-1), BigInt (via tags 2/3 for >64-bit magnitude)
 *      - Uint8List / List<int> (0..255) → byte string
 *      - String (UTF-8) → text string
 *      - List → array
 *      - Map → map (keys must be encodable; sorted deterministically)
 *      - bool, null
 *      - double → encoded as 64-bit IEEE754 (deterministic; not minimal float size)
 *
 *  • Decode accepts definite forms; also tolerates most indefinite forms
 *    (arrays, maps, byte/text strings) and assembles them.
 *
 * Notes:
 *  - Floating-point "preferred serialization" (minimal-size 16/32/64-bit)
 *    is not implemented; we always use 64-bit. If you need exact canonical
 *    minimal floats, extend `_encodeDouble`.
 *
 *  - For BigInt outside uint64 range, we use tags 2 (positive bignum) and 3
 *    (negative bignum) with big-endian magnitude as per RFC 8949 §3.4.3.
 */

import 'dart:convert' show utf8;
import 'dart:typed_data';

class CborException implements Exception {
  final String message;
  CborException(this.message);
  @override
  String toString() => 'CborException: $message';
}

class Cbor {
  /// Encode [value] into deterministic CBOR bytes.
  static Uint8List encode(Object? value) {
    final enc = _Encoder();
    enc.encode(value, deterministic: true);
    return enc.bytes();
  }

  /// Deterministically encode only the key (used for sort comparison).
  static Uint8List encodeKey(Object? key) {
    final enc = _Encoder();
    enc.encode(key, deterministic: true);
    return enc.bytes();
  }

  /// Decode a single CBOR item from [bytes]. Throws on trailing data.
  static Object? decodeOne(Uint8List bytes) {
    final dec = _Decoder(bytes);
    final v = dec.decodeAny();
    if (!dec.isAtEnd) {
      throw CborException('Trailing data after single item');
    }
    return v;
  }

  /// Decode a single CBOR item, returning value and the offset consumed.
  static (Object?, int) decodeWithOffset(Uint8List bytes, {int offset = 0}) {
    final dec = _Decoder(bytes, offset: offset);
    final v = dec.decodeAny();
    return (v, dec.offset);
  }

  /// Hex helper.
  static String toHex(Uint8List bytes, {bool with0x = true}) {
    const hexd = '0123456789abcdef';
    final sb = StringBuffer();
    if (with0x) sb.write('0x');
    for (final b in bytes) {
      sb.write(hexd[(b >> 4) & 0xF]);
      sb.write(hexd[b & 0xF]);
    }
    return sb.toString();
  }
}

// =============================== Encoder =====================================

class _Encoder {
  final BytesBuilder _bb = BytesBuilder();

  Uint8List bytes() => _bb.toBytes();

  void encode(Object? v, {required bool deterministic}) {
    if (v == null) return _writeSimple(22); // null
    if (v is bool) return _writeSimple(v ? 21 : 20);

    if (v is int) return _encodeInt(v);
    if (v is BigInt) return _encodeBigInt(v);

    if (v is String) return _encodeString(v);
    if (v is Uint8List) return _encodeBytes(v);
    if (v is List<int> && v.every((e) => e >= 0 && e <= 255)) {
      return _encodeBytes(Uint8List.fromList(v));
    }

    if (v is List) return _encodeArray(v, deterministic: deterministic);
    if (v is Map) return _encodeMap(v, deterministic: deterministic);

    if (v is double) return _encodeDouble(v);

    throw CborException('Unsupported type for CBOR encode: ${v.runtimeType}');
  }

  void _encodeInt(int n) {
    if (n >= 0) {
      _writeTypeAndLength(0, n); // major 0: unsigned
    } else {
      // CBOR negative integers encode -1-n as unsigned.
      final m = -1 - n;
      if (m < 0) throw CborException('int underflow during neg encoding');
      _writeTypeAndLength(1, m); // major 1: negative
    }
  }

  void _encodeBigInt(BigInt b) {
    // fit into uint64?
    final maxUint64 = BigInt.parse('0xffffffffffffffff');
    if (b >= BigInt.zero && b <= maxUint64) {
      return _writeTypeAndLength(0, _asUint64(b)); // encode as unsigned int
    }
    if (b < BigInt.zero &&
        (-b - BigInt.one) >= BigInt.zero &&
        (-b - BigInt.one) <= maxUint64) {
      final m = (-b - BigInt.one);
      return _writeTypeAndLength(1, _asUint64(m)); // encode as negative int
    }

    // Use tags 2 (positive bignum) / 3 (negative bignum)
    if (b >= BigInt.zero) {
      _writeTypeAndLength(6, 2); // tag 2
      _encodeBytes(_bigIntToBytes(b));
    } else {
      _writeTypeAndLength(6, 3); // tag 3
      final m = (-b) - BigInt.one;
      _encodeBytes(_bigIntToBytes(m));
    }
  }

  void _encodeString(String s) {
    final bs = utf8.encode(s);
    _writeTypeAndLength(3, bs.length);
    _bb.add(bs);
  }

  void _encodeBytes(Uint8List bs) {
    _writeTypeAndLength(2, bs.length);
    _bb.add(bs);
  }

  void _encodeArray(List list, {required bool deterministic}) {
    _writeTypeAndLength(4, list.length);
    for (final e in list) {
      encode(e, deterministic: deterministic);
    }
  }

  void _encodeMap(Map map, {required bool deterministic}) {
    if (!deterministic) {
      _writeTypeAndLength(5, map.length);
      map.forEach((k, v) {
        encode(k, deterministic: deterministic);
        encode(v, deterministic: deterministic);
      });
      return;
    }

    // Deterministic: sort entries by encoded key bytes (lexicographic unsigned)
    final entries = <(Uint8List, Object?, Object?)>[];
    map.forEach((k, v) {
      final keyEnc = _Encoder()..encode(k, deterministic: true);
      entries.add((keyEnc.bytes(), k, v));
    });
    entries.sort((a, b) => _lexCmp(a.$1, b.$1));

    _writeTypeAndLength(5, entries.length);
    for (final e in entries) {
      // reuse pre-encoded key bytes for performance
      _bb.add(e.$1);
      encode(e.$3, deterministic: true);
    }
  }

  void _encodeDouble(double d) {
    // Preferred would be minimal-size float; we always encode as 64-bit.
    final bd = ByteData(8);
    bd.setFloat64(0, d, Endian.big);
    _writeTypeAndLengthRaw(7, 27); // 27 => 8 bytes follow
    _bb.add(bd.buffer.asUint8List());
  }

  // ---- low-level writers ----

  void _writeTypeAndLength(int major, int value) {
    if (value < 0) throw CborException('length/value < 0');
    if (value <= 23) {
      _bb.add([((major & 0x07) << 5) | value]);
    } else if (value <= 0xFF) {
      _bb.add([((major & 0x07) << 5) | 24, value & 0xFF]);
    } else if (value <= 0xFFFF) {
      _bb.add([((major & 0x07) << 5) | 25]);
      final bd = ByteData(2)..setUint16(0, value, Endian.big);
      _bb.add(bd.buffer.asUint8List());
    } else if (value <= 0xFFFFFFFF) {
      _bb.add([((major & 0x07) << 5) | 26]);
      final bd = ByteData(4)..setUint32(0, value, Endian.big);
      _bb.add(bd.buffer.asUint8List());
    } else {
      // write uint64
      _bb.add([((major & 0x07) << 5) | 27]);
      final bd = ByteData(8)..setUint64(0, value, Endian.big);
      _bb.add(bd.buffer.asUint8List());
    }
  }

  void _writeTypeAndLengthRaw(int major, int ai) {
    _bb.add([((major & 0x07) << 5) | (ai & 0x1F)]);
  }

  void _writeSimple(int ai) {
    if (ai < 0 || ai > 31) throw CborException('simple out of range');
    _writeTypeAndLengthRaw(7, ai);
  }
}

// =============================== Decoder =====================================

class _Decoder {
  final Uint8List _bs;
  int offset;

  _Decoder(this._bs, {this.offset = 0});

  bool get isAtEnd => offset >= _bs.length;

  int _readByte() {
    if (offset >= _bs.length) throw CborException('Unexpected EOF');
    return _bs[offset++];
  }

  int _readUintN(int n) {
    if (offset + n > _bs.length) throw CborException('Unexpected EOF');
    final bd = ByteData.sublistView(_bs, offset, offset + n);
    offset += n;
    switch (n) {
      case 1:
        return bd.getUint8(0);
      case 2:
        return bd.getUint16(0, Endian.big);
      case 4:
        return bd.getUint32(0, Endian.big);
      case 8:
        final v = bd.getUint64(0, Endian.big);
        // Dart int is signed 64 on native; keep as int (non-negative)
        return v;
      default:
        throw CborException('Unsupported uint length: $n');
    }
  }

  (int major, int ai, int? length) _readHead() {
    final ib = _readByte();
    final major = (ib >> 5) & 0x07;
    final ai = ib & 0x1F;
    if (ai < 24) return (major, ai, ai);
    if (ai == 24) return (major, ai, _readUintN(1));
    if (ai == 25) return (major, ai, _readUintN(2));
    if (ai == 26) return (major, ai, _readUintN(4));
    if (ai == 27) return (major, ai, _readUintN(8));
    if (ai == 31) return (major, ai, null); // indefinite
    throw CborException('Reserved additional info: $ai');
  }

  Object? decodeAny() {
    final (major, ai, length) = _readHead();
    switch (major) {
      case 0: // unsigned int
        return _asSignedInt(length!); // stays non-negative
      case 1: // negative int: -1 - n
        final n = _asSignedInt(length!);
        return -1 - n;
      case 2: // byte string
        return _decodeBytes(ai, length);
      case 3: // text string
        return _decodeText(ai, length);
      case 4: // array
        return _decodeArray(ai, length);
      case 5: // map
        return _decodeMap(ai, length);
      case 6: // tag
        final tag = length!;
        final tagged = decodeAny();
        return _applyTag(tag, tagged);
      case 7: // floats & simple values
        return _decodeSimple(ai, length);
      default:
        throw CborException('Unknown major type: $major');
    }
  }

  // ---- decoders ----

  Object _decodeBytes(int ai, int? length) {
    if (ai == 31) {
      // indefinite: series of definite byte strings terminated by break
      final chunks = <int>[];
      while (true) {
        final (m2, ai2, len2) = _readHead();
        if (m2 == 7 && ai2 == 31) break; // break
        if (m2 != 2 || len2 == null) {
          throw CborException('Indefinite bytes with non-bytes chunk');
        }
        if (offset + len2 > _bs.length) throw CborException('EOF in bytes');
        chunks.addAll(_bs.sublist(offset, offset + len2));
        offset += len2;
      }
      return Uint8List.fromList(chunks);
    } else {
      final len = length!;
      if (offset + len > _bs.length) throw CborException('EOF in bytes');
      final out = Uint8List.sublistView(_bs, offset, offset + len);
      offset += len;
      return out;
    }
  }

  Object _decodeText(int ai, int? length) {
    if (ai == 31) {
      final sb = StringBuffer();
      while (true) {
        final (m2, ai2, len2) = _readHead();
        if (m2 == 7 && ai2 == 31) break;
        if (m2 != 3 || len2 == null) {
          throw CborException('Indefinite text with non-text chunk');
        }
        if (offset + len2 > _bs.length) throw CborException('EOF in text');
        final slice = _bs.sublist(offset, offset + len2);
        offset += len2;
        sb.write(utf8.decode(slice));
      }
      return sb.toString();
    } else {
      final len = length!;
      if (offset + len > _bs.length) throw CborException('EOF in text');
      final out = _bs.sublist(offset, offset + len);
      offset += len;
      return utf8.decode(out);
    }
  }

  Object _decodeArray(int ai, int? length) {
    if (ai == 31) {
      final out = <Object?>[];
      while (true) {
        final peek = _peekByte();
        if (peek == 0xFF) {
          offset++; // consume break
          break;
        }
        out.add(decodeAny());
      }
      return out;
    } else {
      final len = length!;
      final out = List<Object?>.filled(len, null, growable: false);
      for (var i = 0; i < len; i++) {
        out[i] = decodeAny();
      }
      return out;
    }
  }

  Object _decodeMap(int ai, int? length) {
    if (ai == 31) {
      final out = <Object?, Object?>{};
      while (true) {
        final peek = _peekByte();
        if (peek == 0xFF) {
          offset++; // break
          break;
        }
        final k = decodeAny();
        final v = decodeAny();
        out[k] = v;
      }
      return out;
    } else {
      final len = length!;
      final out = <Object?, Object?>{};
      for (var i = 0; i < len; i++) {
        final k = decodeAny();
        final v = decodeAny();
        out[k] = v;
      }
      return out;
    }
  }

  Object? _decodeSimple(int ai, int? length) {
    if (ai < 20) {
      if (ai == 16 || ai == 17 || ai == 18 || ai == 19) {
        // unassigned; treat as simple
        return _simple(ai);
      }
      return _simple(ai);
    }
    switch (ai) {
      case 20:
        return false;
      case 21:
        return true;
      case 22:
        return null; // null
      case 23:
        return _Undefined.instance; // undefined
      case 24:
        final v = _readUintN(1);
        return _simple(v);
      case 25: // half-precision float
        final h = _readUintN(2);
        return _halfToDouble(h);
      case 26: // single-precision float
        final raw = _readUintN(4);
        final bd = ByteData(4)..setUint32(0, raw, Endian.big);
        return bd.getFloat32(0, Endian.big);
      case 27: // double-precision float
        final hi = _readUintN(4);
        final lo = _readUintN(4);
        final bd = ByteData(8)
          ..setUint32(0, hi, Endian.big)
          ..setUint32(4, lo, Endian.big);
        return bd.getFloat64(0, Endian.big);
      case 31:
        // break (handled by callers that expect it)
        return _Break.instance;
      default:
        return _simple(ai);
    }
  }

  int _peekByte() {
    if (offset >= _bs.length) throw CborException('Unexpected EOF (peek)');
    return _bs[offset];
  }

  Object _applyTag(int tag, Object? value) {
    // Tags 2/3 (bignum) → BigInt
    if (tag == 2 || tag == 3) {
      if (value is! Uint8List) {
        throw CborException('Tag $tag must apply to byte string');
      }
      final mag = _bytesToBigInt(value);
      if (tag == 2) return mag;
      // tag 3 = negative bignum -> -1 - n
      return -(mag + BigInt.one);
    }
    // Unknown tag: return a tuple (tag, value)
    return (tag, value);
  }

  // Convert non-negative encoded uint n into int (preserve full 64-bit range).
  int _asSignedInt(int n) => n;

  static Object _simple(int ai) => (ai); // return the simple value code

  static double _halfToDouble(int h) {
    final s = (h >> 15) & 0x1;
    final e = (h >> 10) & 0x1F;
    final f = h & 0x3FF;

    double v;
    if (e == 0) {
      v = (f == 0) ? 0.0 : (f / 1024.0) * (1 / (1 << 14));
    } else if (e == 31) {
      v = (f == 0) ? double.infinity : double.nan;
    } else {
      v = (1 + f / 1024.0) * (1 << (e - 15));
    }
    return s == 1 ? -v : v;
  }
}

// ============================== Utilities ====================================

int _lexCmp(Uint8List a, Uint8List b) {
  final n = a.length < b.length ? a.length : b.length;
  for (var i = 0; i < n; i++) {
    final ai = a[i], bi = b[i];
    if (ai != bi) return ai - bi;
  }
  return a.length - b.length;
}

Uint8List _bigIntToBytes(BigInt v) {
  if (v < BigInt.zero) throw CborException('bigIntToBytes: negative');
  final bytes = <int>[];
  var n = v;
  while (n > BigInt.zero) {
    final byte = (n & BigInt.from(0xFF)).toInt();
    bytes.add(byte);
    n = n >> 8;
  }
  if (bytes.isEmpty) bytes.add(0);
  return Uint8List.fromList(bytes.reversed.toList());
}

BigInt _bytesToBigInt(Uint8List bs) {
  var n = BigInt.zero;
  for (final b in bs) {
    n = (n << 8) | BigInt.from(b);
  }
  return n;
}

int _asUint64(BigInt v) {
  // Assumes 0 <= v <= 2^64-1
  // Dart int can hold 64-bit signed; but we only pass magnitudes that fit.
  // If > 2^63-1, ByteData.setUint64 will handle it when writing.
  return v.toUnsigned(64).toInt();
}

class _Undefined {
  static const instance = _Undefined._();
  const _Undefined._();
  @override
  String toString() => 'undefined';
}

class _Break {
  static const instance = _Break._();
  const _Break._();
  @override
  String toString() => 'break';
}
