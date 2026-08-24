// EMB1 — the one-line wire format an embed job's result travels in:
//
//   EMB1 <model> <dims> f16 <base64 little-endian float16 N×dims> <sha256 hex>
//
// One line on purpose: the node's submit gates trim trailing low-entropy
// lines and reject multi-char-poor text, and base64 on a single line passes
// every gate untouched. The bridge verifies length + sha256 before trusting
// a single vector.

import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

int _f32ToF16(double value) {
  final f32 = Float32List(1)..[0] = value;
  final x = f32.buffer.asUint32List()[0];
  final sign = (x >> 16) & 0x8000;
  var exp = (x >> 23) & 0xff;
  var mant = x & 0x7fffff;
  if (exp == 0xff) return sign | 0x7c00 | (mant != 0 ? 0x200 : 0);
  exp = exp - 127 + 15;
  if (exp >= 0x1f) return sign | 0x7c00;
  if (exp <= 0) {
    if (exp < -10) return sign;
    mant = (mant | 0x800000) >> (1 - exp);
    return sign | ((mant + 0x1000) >> 13);
  }
  return sign | (exp << 10) | ((mant + 0x1000) >> 13);
}

String encodeEmb1(String model, List<List<double>> vecs) {
  final dims = vecs.isEmpty ? 0 : vecs.first.length;
  final bytes = ByteData(vecs.length * dims * 2);
  var o = 0;
  for (final v in vecs) {
    if (v.length != dims) {
      throw ArgumentError('ragged embedding rows (${v.length} vs $dims)');
    }
    for (final x in v) {
      bytes.setUint16(o, _f32ToF16(x), Endian.little);
      o += 2;
    }
  }
  final raw = bytes.buffer.asUint8List();
  return 'EMB1 $model $dims f16 ${base64Encode(raw)} ${sha256.convert(raw)}';
}

/// Unit vectors are what the bridge and every consumer expect.
List<double> l2norm(List<double> v) {
  var s = 0.0;
  for (final x in v) {
    s += x * x;
  }
  final n = s == 0 ? 1.0 : 1 / math.sqrt(s);
  return [for (final x in v) x * n];
}
