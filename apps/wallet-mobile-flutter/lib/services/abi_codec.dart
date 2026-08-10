// animica:abi:v1 calldata codec — a byte-for-byte Dart port of the chain's
// reference encoder (sdk/python/omni_sdk/types/abi.py), the format the node's
// execution.runtime.contracts._decode_call_data enforces verbatim.
//
// A CALL's `data` bstr is:
//   selector(8) ++ uvarint(argc) ++ encode(arg0) ++ encode(arg1) ++ …
//
//   selector      = sha3_256("animica:abi:v1|" + name + "(" + t0,t1,… + ")")[:8]
//                   where each ti is the *literal* canonical type string as
//                   written in the manifest ("int", "bytes", "bool", …) — NOT
//                   base-normalized. `transfer(bytes,int)`, not
//                   `transfer(bytes,int256)`.
//   uvarint        = LEB128 (omni_sdk.utils.bytes.uvarint_encode)
//   uint<b>        = uvarint(len(minmag)) ++ minmag   (minmag of 0 is 0x00)
//   int<b>         = uvarint(len(sign++minmag)) ++ sign ++ minmag
//                    sign = 0x00 (>=0) | 0x01 (<0), magnitude = |value|
//   bool           = 0x00 | 0x01                       (no length prefix)
//   address/string = uvarint(len(utf8)) ++ utf8
//   bytes          = uvarint(len) ++ raw
//   hash           = 32 raw bytes                       (no length prefix)
//   bytesN         = N raw bytes                        (no length prefix)
//   T[] / T[k]     = uvarint(count) ++ encode(items…)
//   (T0,T1,…)      = uvarint(nelems) ++ encode(fields…)
//
// Integers are `int`/`uint` aliases: bare `int`→int256, `uint`→uint256,
// `i32`→int32, `u64`→uint64. The DEX/token ABIs use only scalar args, but
// arrays/tuples are supported so this stays a faithful mirror.
//
// Verified against Python golden vectors in test/abi_codec_test.dart.

import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha3.dart';

class AbiError implements Exception {
  final String message;
  const AbiError(this.message);
  @override
  String toString() => 'AbiError: $message';
}

/// One function entry from a manifest ABI (`abi.functions[i]`).
class AbiFn {
  final String name;
  final List<AbiParam> inputs;
  final List<AbiParam> outputs;
  const AbiFn({required this.name, this.inputs = const [], this.outputs = const []});

  factory AbiFn.fromJson(Map<String, dynamic> j) => AbiFn(
        name: j['name'] as String,
        inputs: _params(j['inputs']),
        outputs: _params(j['outputs']),
      );

  static List<AbiParam> _params(dynamic list) {
    if (list is! List) return const [];
    return list
        .whereType<Map>()
        .map((m) => AbiParam(
              name: (m['name'] ?? '') as String,
              type: m['type'] as String,
            ))
        .toList();
  }
}

class AbiParam {
  final String name;
  final String type;
  const AbiParam({required this.name, required this.type});
}

/// Parse a manifest ABI object (`{"functions": [...], "events": [...]}`) into
/// the function entries the codec needs. Tolerates a bare list of entries too.
List<AbiFn> parseAbiFunctions(dynamic abi) {
  final List raw;
  if (abi is Map && abi['functions'] is List) {
    raw = abi['functions'] as List;
  } else if (abi is List) {
    raw = abi.where((e) => e is Map && (e['type'] == null || e['type'] == 'function')).toList();
  } else {
    throw const AbiError('unrecognized ABI shape');
  }
  return raw.whereType<Map>().map((m) => AbiFn.fromJson(Map<String, dynamic>.from(m))).toList();
}

// --------------------------------------------------------------------------
// Type strings
// --------------------------------------------------------------------------

const Set<String> _baseTypes = {
  'bool', 'address', 'hash', 'bytes', 'string',
  'uint8', 'uint16', 'uint32', 'uint64', 'uint128', 'uint256',
  'int8', 'int16', 'int32', 'int64', 'int128', 'int256',
};

final RegExp _arraySuffix = RegExp(r'(\[\]|\[\d+\])$');
final RegExp _uAlias = RegExp(r'^u(\d+)$');
final RegExp _iAlias = RegExp(r'^i(\d+)$');

/// Peel `[]` / `[k]` suffixes; returns (base, dims) innermost-first-appended.
(String, List<int?>) _peelArrays(String t) {
  final dims = <int?>[];
  var cur = t;
  while (true) {
    final m = _arraySuffix.firstMatch(cur);
    if (m == null) break;
    final suffix = m.group(1)!;
    cur = cur.substring(0, cur.length - suffix.length);
    if (suffix == '[]') {
      dims.add(null);
    } else {
      final size = int.parse(suffix.substring(1, suffix.length - 1));
      if (size <= 0) throw const AbiError('fixed array dimension must be positive');
      dims.add(size);
    }
  }
  return (cur, dims);
}

String _normalizeBase(String t) {
  if (t == 'uint') return 'uint256';
  if (t == 'int') return 'int256';
  final mu = _uAlias.firstMatch(t);
  if (mu != null) return 'uint${mu.group(1)}';
  final mi = _iAlias.firstMatch(t);
  if (mi != null) return 'int${mi.group(1)}';
  return t;
}

/// A parsed, resolved type used for encoding/decoding.
sealed class _Ty {
  const _Ty();
}

class _Scalar extends _Ty {
  final String base; // normalized: uint256/int64/bytes/bytesN/bool/address/string/hash
  const _Scalar(this.base);
}

class _Array extends _Ty {
  final _Ty element;
  final int? fixed; // null = dynamic
  const _Array(this.element, this.fixed);
}

class _Tuple extends _Ty {
  final List<_Ty> elements;
  const _Tuple(this.elements);
}

_Ty _parseType(String typeStr) {
  final cleaned = typeStr.trim().toLowerCase().replaceAll(RegExp(r'\s+'), '');
  final (peeled, dims) = _peelArrays(cleaned);
  _Ty inner;
  if (peeled.startsWith('(') && peeled.endsWith(')')) {
    inner = _Tuple(_splitTopLevel(peeled.substring(1, peeled.length - 1))
        .map(_parseType)
        .toList());
  } else {
    final base = _normalizeBase(peeled);
    if (!_baseTypes.contains(base)) {
      // bytesN (1..32)?
      if (base.startsWith('bytes') && base.length > 5) {
        final n = int.tryParse(base.substring(5));
        if (n == null || n < 1 || n > 32) throw AbiError('unsupported base type: $peeled');
      } else {
        throw AbiError('unsupported base type: $peeled');
      }
    }
    inner = _Scalar(base);
  }
  // _peelArrays collects suffixes right-to-left, so dims = [last-suffix, …,
  // first-suffix]. In omni_sdk the LAST suffix (dims[0] there) is the OUTERMOST
  // length: `int[2][3]` → 3 outer × 2 inner. Wrap in reverse so the last-peeled
  // dim ends up outermost, matching the chain codec exactly.
  for (final d in dims.reversed) {
    inner = _Array(inner, d);
  }
  return inner;
}

List<String> _splitTopLevel(String s) {
  final out = <String>[];
  var depth = 0;
  final buf = StringBuffer();
  for (final ch in s.split('')) {
    if (ch == '(') {
      depth++;
      buf.write(ch);
    } else if (ch == ')') {
      depth--;
      if (depth < 0) throw const AbiError('unbalanced parentheses');
      buf.write(ch);
    } else if (ch == ',' && depth == 0) {
      out.add(buf.toString().trim());
      buf.clear();
    } else {
      buf.write(ch);
    }
  }
  if (buf.isNotEmpty) out.add(buf.toString().trim());
  return out;
}

/// The literal canonical type string used inside a function signature —
/// lowercased and whitespace-stripped but NOT base-normalized (`int` stays
/// `int`), exactly as omni_sdk.types.abi.canonical_type returns.
String _canonicalTypeLiteral(String typeStr) {
  final cleaned = typeStr.trim().toLowerCase().replaceAll(RegExp(r'\s+'), '');
  _parseType(cleaned); // validate
  return cleaned;
}

String canonicalFnSignature(AbiFn fn) {
  final types = fn.inputs.map((p) => _canonicalTypeLiteral(p.type)).join(',');
  return '${fn.name}($types)';
}

// --------------------------------------------------------------------------
// Hash + varint primitives
// --------------------------------------------------------------------------

Uint8List _sha3_256(Uint8List input) {
  final d = SHA3Digest(256);
  final out = Uint8List(32);
  d.update(input, 0, input.length);
  d.doFinal(out, 0);
  return out;
}

Uint8List functionSelector(AbiFn fn) {
  final sig = 'animica:abi:v1|${canonicalFnSignature(fn)}';
  return Uint8List.sublistView(_sha3_256(Uint8List.fromList(utf8.encode(sig))), 0, 8);
}

Uint8List uvarintEncode(int n) {
  if (n < 0) throw const AbiError('uvarint expects a non-negative integer');
  final out = <int>[];
  var v = n;
  while (true) {
    var b = v & 0x7f;
    v >>= 7;
    if (v != 0) {
      out.add(b | 0x80);
    } else {
      out.add(b);
      break;
    }
  }
  return Uint8List.fromList(out);
}

/// Returns (value, bytesConsumed) for a LEB128 varint at [offset].
///
/// ABI varints here are lengths/counts, always well within a safe positive
/// range. Dart's native `int` is 64-bit two's-complement, so a hostile 10-byte
/// varint at shift 63 would wrap to a negative value; reject anything past 56
/// bits (9× 7-bit groups) and any negative accumulation rather than silently
/// producing a negative length that later slices out of bounds.
(int, int) uvarintDecode(Uint8List buf, int offset) {
  var result = 0;
  var shift = 0;
  var i = offset;
  while (true) {
    if (i >= buf.length) throw const AbiError('truncated uvarint');
    if (shift > 56) throw const AbiError('uvarint too large');
    final b = buf[i];
    result |= (b & 0x7f) << shift;
    i++;
    if ((b & 0x80) == 0) break;
    shift += 7;
  }
  if (result < 0) throw const AbiError('uvarint overflow');
  return (result, i - offset);
}

Uint8List _minimalUnsigned(BigInt value) {
  if (value < BigInt.zero) throw const AbiError('unsigned cannot be negative');
  if (value == BigInt.zero) return Uint8List.fromList([0]);
  final bytes = <int>[];
  var v = value;
  final mask = BigInt.from(0xff);
  while (v > BigInt.zero) {
    bytes.add((v & mask).toInt());
    v = v >> 8;
  }
  return Uint8List.fromList(bytes.reversed.toList());
}

// --------------------------------------------------------------------------
// Encode
// --------------------------------------------------------------------------

BigInt _toBigInt(Object? v) {
  if (v is BigInt) return v;
  if (v is int) return BigInt.from(v);
  throw AbiError('expected integer, got ${v.runtimeType}');
}

Uint8List _bytesArg(Object? v, {int? fixedLen}) {
  Uint8List data;
  if (v is Uint8List) {
    data = v;
  } else if (v is List<int>) {
    data = Uint8List.fromList(v);
  } else if (v is String) {
    var s = v.startsWith('0x') || v.startsWith('0X') ? v.substring(2) : v;
    if (s.length.isOdd) throw const AbiError('odd-length hex');
    final out = Uint8List(s.length ~/ 2);
    for (var i = 0; i < out.length; i++) {
      out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
    }
    data = out;
  } else {
    throw const AbiError('expected bytes or 0x-hex string');
  }
  if (fixedLen != null && data.length != fixedLen) {
    throw AbiError('expected $fixedLen bytes, got ${data.length}');
  }
  return data;
}

Uint8List _encodeScalar(Object? value, String typ) {
  if (typ.startsWith('uint')) {
    final bits = int.parse(typ.substring(4));
    final val = _toBigInt(value);
    final maxV = (BigInt.one << bits) - BigInt.one;
    if (val < BigInt.zero || val > maxV) throw AbiError('$typ out of range');
    final mag = _minimalUnsigned(val);
    return _concat([uvarintEncode(mag.length), mag]);
  }
  if (typ.startsWith('int')) {
    final bits = int.parse(typ.substring(3));
    final val = _toBigInt(value);
    final minV = -(BigInt.one << (bits - 1));
    final maxV = (BigInt.one << (bits - 1)) - BigInt.one;
    if (val < minV || val > maxV) throw AbiError('$typ out of range');
    final sign = val >= BigInt.zero ? 0x00 : 0x01;
    final mag = _minimalUnsigned(val.abs());
    final payload = _concat([Uint8List.fromList([sign]), mag]);
    return _concat([uvarintEncode(payload.length), payload]);
  }
  if (typ == 'bool') {
    if (value is! bool) throw const AbiError('bool requires a boolean');
    return Uint8List.fromList([value ? 1 : 0]);
  }
  if (typ == 'address' || typ == 'string') {
    if (value is! String) throw AbiError('$typ requires a string');
    final enc = Uint8List.fromList(utf8.encode(value));
    return _concat([uvarintEncode(enc.length), enc]);
  }
  if (typ == 'bytes') {
    final data = _bytesArg(value);
    return _concat([uvarintEncode(data.length), data]);
  }
  if (typ == 'hash') {
    return _bytesArg(value, fixedLen: 32);
  }
  if (typ.startsWith('bytes')) {
    final n = int.parse(typ.substring(5));
    return _bytesArg(value, fixedLen: n);
  }
  throw AbiError('unsupported ABI scalar type: $typ');
}

Uint8List _encodeTy(Object? value, _Ty ty) {
  switch (ty) {
    case _Scalar(:final base):
      return _encodeScalar(value, base);
    case _Array(:final element, :final fixed):
      if (value is! List) throw const AbiError('array argument must be a list');
      if (fixed != null && value.length != fixed) {
        throw AbiError('array length mismatch: expected $fixed, got ${value.length}');
      }
      final parts = <Uint8List>[uvarintEncode(value.length)];
      for (final item in value) {
        parts.add(_encodeTy(item, element));
      }
      return _concat(parts);
    case _Tuple(:final elements):
      if (value is! List) throw const AbiError('tuple argument must be a list');
      if (value.length != elements.length) {
        throw AbiError('tuple length mismatch: expected ${elements.length}, got ${value.length}');
      }
      final parts = <Uint8List>[uvarintEncode(elements.length)];
      for (var i = 0; i < elements.length; i++) {
        parts.add(_encodeTy(value[i], elements[i]));
      }
      return _concat(parts);
  }
}

/// Encode a full call: selector ++ uvarint(argc) ++ encoded args.
Uint8List encodeCall(List<AbiFn> abi, String fnName, List<Object?> args) {
  final fn = abi.firstWhere(
    (f) => f.name == fnName,
    orElse: () => throw AbiError('function not found in ABI: $fnName'),
  );
  if (fn.inputs.length != args.length) {
    throw AbiError('$fnName expects ${fn.inputs.length} argument(s), got ${args.length}');
  }
  final parts = <Uint8List>[functionSelector(fn), uvarintEncode(fn.inputs.length)];
  for (var i = 0; i < args.length; i++) {
    parts.add(_encodeTy(args[i], _parseType(fn.inputs[i].type)));
  }
  return _concat(parts);
}

// --------------------------------------------------------------------------
// Decode return payloads
// --------------------------------------------------------------------------

(Object?, int) _decodeScalar(Uint8List buf, String typ, int offset) {
  if (typ.startsWith('uint')) {
    final (len, c) = uvarintDecode(buf, offset);
    var o = offset + c;
    final payload = _slice(buf, o, len);
    o += len;
    return (_beUnsigned(payload), o);
  }
  if (typ.startsWith('int')) {
    final (len, c) = uvarintDecode(buf, offset);
    var o = offset + c;
    final payload = _slice(buf, o, len);
    o += len;
    if (payload.isEmpty) throw const AbiError('truncated signed integer');
    final mag = _beUnsigned(payload.length > 1 ? Uint8List.sublistView(payload, 1) : Uint8List.fromList([0]));
    return (payload[0] == 0x01 ? -mag : mag, o);
  }
  if (typ == 'bool') {
    final payload = _slice(buf, offset, 1);
    return (payload[0] == 0x01, offset + 1);
  }
  if (typ == 'address' || typ == 'string') {
    final (len, c) = uvarintDecode(buf, offset);
    var o = offset + c;
    final payload = _slice(buf, o, len);
    o += len;
    return (utf8.decode(payload), o);
  }
  if (typ == 'bytes') {
    final (len, c) = uvarintDecode(buf, offset);
    var o = offset + c;
    final payload = _slice(buf, o, len);
    o += len;
    return (payload, o);
  }
  if (typ == 'hash') {
    return (_slice(buf, offset, 32), offset + 32);
  }
  if (typ.startsWith('bytes')) {
    final n = int.parse(typ.substring(5));
    return (_slice(buf, offset, n), offset + n);
  }
  throw AbiError('unsupported ABI scalar type: $typ');
}

(Object?, int) _decodeTy(Uint8List buf, _Ty ty, int offset) {
  switch (ty) {
    case _Scalar(:final base):
      return _decodeScalar(buf, base, offset);
    case _Array(:final element, :final fixed):
      final (count, c) = uvarintDecode(buf, offset);
      var o = offset + c;
      if (fixed != null && count != fixed) {
        throw AbiError('array length mismatch: expected $fixed, got $count');
      }
      final items = <Object?>[];
      for (var i = 0; i < count; i++) {
        final (v, no) = _decodeTy(buf, element, o);
        items.add(v);
        o = no;
      }
      return (items, o);
    case _Tuple(:final elements):
      final (count, c) = uvarintDecode(buf, offset);
      var o = offset + c;
      if (count != elements.length) {
        throw AbiError('tuple length mismatch: expected ${elements.length}, got $count');
      }
      final values = <Object?>[];
      for (final e in elements) {
        final (v, no) = _decodeTy(buf, e, o);
        values.add(v);
        o = no;
      }
      return (values, o);
  }
}

/// Decode a view/return payload: uvarint(count) ++ encoded outputs. Returns the
/// lone value for a single-output function, a `List` for multiple, or null.
/// Integers come back as [BigInt]; `bytes` as [Uint8List]; `address`/`string`
/// as [String].
Object? decodeReturn(List<AbiFn> abi, String fnName, Uint8List data) {
  final fn = abi.firstWhere(
    (f) => f.name == fnName,
    orElse: () => throw AbiError('function not found in ABI: $fnName'),
  );
  final outputs = fn.outputs;
  if (outputs.isEmpty && (data.isEmpty || (data.length == 1 && data[0] == 0))) {
    return null;
  }
  final (count, c) = uvarintDecode(data, 0);
  var offset = c;
  if (count != outputs.length) {
    throw AbiError('return count mismatch: expected ${outputs.length}, got $count');
  }
  final values = <Object?>[];
  for (final out in outputs) {
    final (v, no) = _decodeTy(data, _parseType(out.type), offset);
    values.add(v);
    offset = no;
  }
  if (offset != data.length) throw const AbiError('trailing bytes in return payload');
  if (values.isEmpty) return null;
  if (values.length == 1) return values.first;
  return values;
}

// --------------------------------------------------------------------------
// small byte helpers
// --------------------------------------------------------------------------

Uint8List _concat(List<Uint8List> parts) {
  var total = 0;
  for (final p in parts) {
    total += p.length;
  }
  final out = Uint8List(total);
  var o = 0;
  for (final p in parts) {
    out.setRange(o, o + p.length, p);
    o += p.length;
  }
  return out;
}

Uint8List _slice(Uint8List buf, int offset, int len) {
  if (offset + len > buf.length) throw const AbiError('truncated ABI payload');
  return Uint8List.sublistView(buf, offset, offset + len);
}

BigInt _beUnsigned(Uint8List bytes) {
  var v = BigInt.zero;
  for (final b in bytes) {
    v = (v << 8) | BigInt.from(b);
  }
  return v;
}
