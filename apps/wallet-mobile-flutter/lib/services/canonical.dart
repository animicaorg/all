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
//       + len_prefix(msg)                  // canonical-CBOR-encoded tx body
//   prehash = SHA3-512(raw)                // 64 bytes
//
// The signer then runs the prehash through the alg's backend (SPHINCS-128s
// in our case — see services/keys.dart).
//
// `pack_signed` produces the broadcast envelope:
//   { "body": <tx body map>, "sig": { "algId": int, "pubkey": bytes, "sig": bytes } }
// CBOR-encoded canonically, then submitted via tx.sendRawTransaction.

import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/digests/sha3.dart';

// ── CBOR canonical encoder ─────────────────────────────────────────────
//
// Canonical rules (CBOR canonical = RFC 8949 §4.2.1 / "deterministic"):
//   - Integers use the shortest valid encoding.
//   - Map keys are sorted by their CBOR-encoded byte representation, but
//     Animica's omni_sdk uses the "length-then-lex" order (sort keys by
//     length first, then lexicographically). The chain accepts both, but
//     to be byte-stable with the Python `canonical_body_dict` output we
//     match length-then-lex.

Uint8List canonicalCbor(Object? value) {
  final out = BytesBuilder();
  _encode(out, value);
  return out.toBytes();
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
    if (v >= 0) {
      _encodeHead(out, 0, BigInt.from(v));
    } else {
      _encodeHead(out, 1, BigInt.from(-1 - v));
    }
    return;
  }
  if (v is BigInt) {
    if (v.sign >= 0) {
      _encodeHead(out, 0, v);
    } else {
      _encodeHead(out, 1, -BigInt.one - v);
    }
    return;
  }
  if (v is String) {
    final bytes = utf8.encode(v);
    _encodeHead(out, 3, BigInt.from(bytes.length));
    out.add(bytes);
    return;
  }
  if (v is Uint8List || v is List<int>) {
    final bytes = v is Uint8List ? v : Uint8List.fromList(v as List<int>);
    _encodeHead(out, 2, BigInt.from(bytes.length));
    out.add(bytes);
    return;
  }
  if (v is Map) {
    final entries = v.entries.toList();
    entries.sort((a, b) => _cmpKey(a.key, b.key));
    _encodeHead(out, 5, BigInt.from(entries.length));
    for (final e in entries) {
      _encode(out, e.key);
      _encode(out, e.value);
    }
    return;
  }
  if (v is List) {
    _encodeHead(out, 4, BigInt.from(v.length));
    for (final x in v) {
      _encode(out, x);
    }
    return;
  }
  throw ArgumentError('canonicalCbor: unsupported type ${v.runtimeType}');
}

int _cmpKey(Object a, Object b) {
  if (a is String && b is String) {
    if (a.length != b.length) return a.length.compareTo(b.length);
    return a.compareTo(b);
  }
  // Fall back to a stable ordering.
  return a.toString().compareTo(b.toString());
}

void _encodeHead(BytesBuilder out, int major, BigInt v) {
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
