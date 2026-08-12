// Canonical CBOR encoder + Animica's `build_sign_bytes` wrapper.
//
// The chain validates tx signatures over an exact byte string defined by
// `pq/py/sign.py:build_sign_bytes`:
//
//   raw = len_prefix(b"animica:sign/v1")
//       + len_prefix(domain_b)             // e.g. "tx"
//       + len_prefix(uvarint(chain_id))    // empty if null
//       + len_prefix(uvarint(fork_id))     // empty if null
//       + len_prefix(uvarint(alg_id))
//       + len_prefix(context)              // empty bytes for tx
//       + len_prefix(msg)                  // tx_signing_preimage bytes
//   prehash = SHA3-512(raw)                // 64 bytes
//
// `msg` is NOT the tx body: it is the canonical signing preimage
// (`signer.dart:txSigningPreimage`), which wraps the normalized body together
// with the chain id, genesis hash and network name. The signer then runs the
// prehash through the alg's backend (ML-DSA-65 — see services/ml_dsa_65.dart).
//
// `pack_signed` produces the broadcast envelope:
//   { "body": <tx body map>, "sig": { "algId": int, "pubkey": bytes, "sig": bytes } }
// CBOR-encoded canonically, then submitted via tx.sendRawTransaction.

import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha3.dart';

// ── CBOR canonical encoder ─────────────────────────────────────────────
//
// Canonical rules (CBOR canonical = RFC 8949 §4.2.1 / "deterministic"),
// matching `core/encoding/cbor.py:dumps` field for field:
//   - Integers use the shortest valid encoding; magnitudes above 2^64-1 use
//     the bignum tags 2 (positive) / 3 (negative), same as `_to_bignum_bytes`.
//   - Map keys are pre-encoded and sorted by the LEXICOGRAPHIC ORDER OF THOSE
//     ENCODED BYTES. For text keys shorter than 2^8 that is identical to the
//     "length-then-lex" order the omni_sdk describes (the length lives in the
//     head byte), and it is also correct for the INTEGER keys 1..7 of the
//     signing preimage, where a `toString()` comparison would silently
//     mis-order anything past single digits.
//   - Duplicate keys (after encoding) are rejected rather than emitted.

Uint8List canonicalCbor(Object? value) {
  final out = BytesBuilder();
  _encode(out, value);
  return out.toBytes();
}

Uint8List _encoded(Object? v) {
  final b = BytesBuilder();
  _encode(b, v);
  return b.toBytes();
}

void _encode(BytesBuilder out, Object? v) {
  if (v == null) {
    out.addByte(0xf6);
    return;
  }
  if (v is bool) {
    out.addByte(v ? 0xf5 : 0xf4);
    return;
  }
  if (v is int) {
    _encodeInt(out, BigInt.from(v));
    return;
  }
  if (v is BigInt) {
    _encodeInt(out, v);
    return;
  }
  if (v is String) {
    final bytes = utf8.encode(v);
    _encodeHead(out, 3, bytes.length);
    out.add(bytes);
    return;
  }
  if (v is Uint8List || v is List<int>) {
    final bytes = v is Uint8List ? v : Uint8List.fromList(v as List<int>);
    _encodeHead(out, 2, bytes.length);
    out.add(bytes);
    return;
  }
  if (v is Map) {
    final pairs = <List<Uint8List>>[];
    for (final e in v.entries) {
      final k = e.key;
      if (k is! int && k is! BigInt && k is! String && k is! Uint8List) {
        throw ArgumentError(
            'canonicalCbor: unsupported map key type ${k.runtimeType}');
      }
      pairs.add(<Uint8List>[_encoded(k), _encoded(e.value)]);
    }
    pairs.sort((a, b) => _cmpBytes(a[0], b[0]));
    for (var i = 1; i < pairs.length; i++) {
      if (_cmpBytes(pairs[i - 1][0], pairs[i][0]) == 0) {
        throw ArgumentError('canonicalCbor: duplicate map key after encoding');
      }
    }
    _encodeHead(out, 5, pairs.length);
    for (final p in pairs) {
      out.add(p[0]);
      out.add(p[1]);
    }
    return;
  }
  if (v is List) {
    _encodeHead(out, 4, v.length);
    for (final x in v) {
      _encode(out, x);
    }
    return;
  }
  throw ArgumentError('canonicalCbor: unsupported type ${v.runtimeType}');
}

final BigInt _uint64Max = BigInt.parse('18446744073709551615');

void _encodeInt(BytesBuilder out, BigInt v) {
  if (v.sign >= 0) {
    if (v <= _uint64Max) {
      _encodeBigHead(out, 0, v);
    } else {
      // Positive bignum: tag(2) + bstr(minimal big-endian magnitude).
      _encodeHead(out, 6, 2);
      final mag = _magnitudeBytes(v);
      _encodeHead(out, 2, mag.length);
      out.add(mag);
    }
    return;
  }
  final m = -BigInt.one - v;
  if (m <= _uint64Max) {
    _encodeBigHead(out, 1, m);
  } else {
    // Negative bignum: tag(3) + bstr(magnitude of -1 - n).
    _encodeHead(out, 6, 3);
    final mag = _magnitudeBytes(m);
    _encodeHead(out, 2, mag.length);
    out.add(mag);
  }
}

Uint8List _magnitudeBytes(BigInt n) {
  assert(n >= BigInt.zero);
  if (n == BigInt.zero) return Uint8List.fromList(<int>[0]);
  final bytes = <int>[];
  var rem = n;
  final mask = BigInt.from(0xff);
  while (rem > BigInt.zero) {
    bytes.insert(0, (rem & mask).toInt());
    rem = rem >> 8;
  }
  return Uint8List.fromList(bytes);
}

int _cmpBytes(Uint8List a, Uint8List b) {
  final n = a.length < b.length ? a.length : b.length;
  for (var i = 0; i < n; i++) {
    if (a[i] != b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}

void _encodeHead(BytesBuilder out, int major, int n) =>
    _encodeBigHead(out, major, BigInt.from(n));

void _encodeBigHead(BytesBuilder out, int major, BigInt v) {
  final m = major << 5;
  if (v < BigInt.from(24)) {
    out.addByte(m | v.toInt());
  } else if (v <= BigInt.from(0xff)) {
    out.addByte(m | 24);
    out.addByte(v.toInt());
  } else if (v <= BigInt.from(0xffff)) {
    out.addByte(m | 25);
    final n = v.toInt();
    out.addByte((n >> 8) & 0xff);
    out.addByte(n & 0xff);
  } else if (v <= BigInt.from(0xffffffff)) {
    out.addByte(m | 26);
    final n = v.toInt();
    out.addByte((n >> 24) & 0xff);
    out.addByte((n >> 16) & 0xff);
    out.addByte((n >> 8) & 0xff);
    out.addByte(n & 0xff);
  } else {
    out.addByte(m | 27);
    // 8-byte big-endian
    var rem = v;
    final bytes = List<int>.filled(8, 0);
    for (var i = 7; i >= 0; i--) {
      bytes[i] = (rem & BigInt.from(0xff)).toInt();
      rem = rem >> 8;
    }
    out.add(bytes);
  }
}

// ── varint + sign-bytes ─────────────────────────────────────────────────

Uint8List uvarint(int v) {
  final out = <int>[];
  var n = v;
  while (n >= 0x80) {
    out.add((n & 0x7f) | 0x80);
    n >>= 7;
  }
  out.add(n & 0x7f);
  return Uint8List.fromList(out);
}

Uint8List _lenPrefix(Uint8List b) {
  final len = uvarint(b.length);
  final out = Uint8List(len.length + b.length);
  out.setRange(0, len.length, len);
  out.setRange(len.length, out.length, b);
  return out;
}

Uint8List _concat(List<Uint8List> parts) {
  final n = parts.fold<int>(0, (a, b) => a + b.length);
  final out = Uint8List(n);
  var off = 0;
  for (final p in parts) {
    out.setRange(off, off + p.length, p);
    off += p.length;
  }
  return out;
}

Uint8List sha3_512(Uint8List input) {
  final d = SHA3Digest(512);
  d.update(input, 0, input.length);
  final out = Uint8List(64);
  d.doFinal(out, 0);
  return out;
}

/// Compute the prehash that the PQ backend signs.
///
/// `msg` is typically the canonical-CBOR-encoded tx body.
Uint8List buildSignBytes({
  required Uint8List msg,
  required int algId,
  String domain = 'tx',
  int? chainId,
  int? forkId,
  Uint8List? context,
}) {
  final tag = utf8.encode('animica:sign/v1');
  final dom = utf8.encode(domain);
  final chainEnc = chainId == null ? Uint8List(0) : uvarint(chainId);
  final forkEnc = forkId == null ? Uint8List(0) : uvarint(forkId);
  final algEnc = uvarint(algId);
  final ctx = context ?? Uint8List(0);
  final raw = _concat([
    _lenPrefix(Uint8List.fromList(tag)),
    _lenPrefix(Uint8List.fromList(dom)),
    _lenPrefix(chainEnc),
    _lenPrefix(forkEnc),
    _lenPrefix(algEnc),
    _lenPrefix(ctx),
    _lenPrefix(msg),
  ]);
  return sha3_512(raw);
}

/// Build the CBOR-encoded broadcast envelope.
///
///   { "body": <txBody>, "sig": { "algId": int, "pubkey": bytes, "sig": bytes } }
Uint8List packSignedEnvelope({
  required Map<String, dynamic> body,
  required int algId,
  required Uint8List publicKey,
  required Uint8List signature,
}) {
  final env = <String, dynamic>{
    'body': body,
    'sig': <String, dynamic>{
      'algId': algId,
      'pubkey': publicKey,
      'sig': signature,
    },
  };
  return canonicalCbor(env);
}
