/*
 * Animica Wallet — Light Client (subset)
 *
 * What this does:
 *  • Parse & sanity-check headers (number/timestamp monotonic, parent linkage).
 *  • Verify a contiguous chain of headers against a trusted checkpoint.
 *  • Verify DA (Data-Availability) proofs against a header’s daRoot using a
 *    simple SHA-256 binary Merkle check (compatible with our DA client helper).
 *
 * What this does NOT do (yet):
 *  • Full consensus (PoIES) validation, difficulty/weight checks, or signatures.
 *  • Canonical header hashing (RLP/CBOR). We trust `hash` provided by the node.
 *
 * RPC hints (best-effort):
 *  - animica_getHeaderByNumber(number | 0xHEX, includeBodies=false)
 *  - animica_getHeaderByHash(0xHASH, includeBodies=false)
 *  Fallbacks (ETH-like names) are wired if _USE_ETH_NAMES is set.
 */

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' show sha256;

import 'rpc_client.dart';

const bool _USE_ETH_NAMES = false;

final _M_GET_HDR_BY_NUM = _USE_ETH_NAMES ? 'eth_getBlockByNumber' : 'animica_getHeaderByNumber';
final _M_GET_HDR_BY_HASH = _USE_ETH_NAMES ? 'eth_getBlockByHash'   : 'animica_getHeaderByHash';

/// Minimal header view for light verification.
class LightHeader {
  final String hash;        // 0x…
  final String parentHash;  // 0x…
  final int number;         // height
  final int timestamp;      // seconds
  final String? daRoot;     // 0x… (root of DA tree)
  final String? txRoot;     // 0x…
  final String? stateRoot;  // 0x…
  final String? receiptsRoot; // 0x…
  final Map<String, dynamic> raw; // original JSON (for debugging)

  LightHeader({
    required this.hash,
    required this.parentHash,
    required this.number,
    required this.timestamp,
    this.daRoot,
    this.txRoot,
    this.stateRoot,
    this.receiptsRoot,
    required this.raw,
  });

  factory LightHeader.fromJson(Map<String, dynamic> m) {
    Map<String, dynamic> mm = m;
    // Some nodes wrap {header:{...}}
    if (m['header'] is Map) {
      mm = (m['header'] as Map).map((k, v) => MapEntry(k.toString(), v));
    }

    String _s(key1, [key2]) {
      final v = mm[key1] ?? (key2 != null ? mm[key2] : null);
      return v == null ? '' : v.toString();
    }

    return LightHeader(
      hash: _s('hash'),
      parentHash: _s('parentHash', 'parent_hash'),
      number: _asInt(mm['number'] ?? mm['height']),
      timestamp: _asInt(mm['timestamp']),
      daRoot: _maybeHex(mm['daRoot'] ?? mm['da_root'] ?? mm['blobRoot']),
      txRoot: _maybeHex(mm['transactionsRoot'] ?? mm['txRoot']),
      stateRoot: _maybeHex(mm['stateRoot'] ?? mm['state_root']),
      receiptsRoot: _maybeHex(mm['receiptsRoot'] ?? mm['receipts_root']),
      raw: mm,
    );
  }

  @override
  String toString() =>
      'Header{#${number}, ts=$timestamp, hash=${_short(hash)}, parent=${_short(parentHash)}, daRoot=${_short(daRoot)} }';
}

/// Verification result with context.
class VerifyResult {
  final bool ok;
  final String message;
  const VerifyResult(this.ok, this.message);

  @override
  String toString() => ok ? 'OK: $message' : 'FAIL: $message';

  static VerifyResult okMsg(String m) => VerifyResult(true, m);
  static VerifyResult fail(String m) => VerifyResult(false, m);
}

/// A tiny light client focused on structural checks + DA proof checks.
class LightClient {
  final RpcClient rpc;
  LightClient(this.rpc);

  factory LightClient.fromEnv() => LightClient(RpcClient.fromEnv());

  // ----------------- Fetch helpers -----------------

  Future<LightHeader?> fetchByNumber(int number) async {
    final param = '0x${number.toRadixString(16)}';
    final res = await rpc.call<dynamic>(_M_GET_HDR_BY_NUM, _USE_ETH_NAMES ? [param, false] : [param]);
    if (res == null) return null;
    return LightHeader.fromJson(_coerceMap(res));
  }

  Future<LightHeader?> fetchByHash(String hash) async {
    final h = _normHex(hash);
    final res = await rpc.call<dynamic>(_M_GET_HDR_BY_HASH, _USE_ETH_NAMES ? [h, false] : [h]);
    if (res == null) return null;
    return LightHeader.fromJson(_coerceMap(res));
  }

  // ----------------- Header checks -----------------

  /// Verify that [child] correctly links to [parent] and time/height are monotonic.
  VerifyResult verifyLinkage(LightHeader parent, LightHeader child) {
    if (!_hexEq(child.parentHash, parent.hash)) {
      return VerifyResult.fail('parentHash mismatch: child.parent=${child.parentHash} vs parent.hash=${parent.hash}');
    }
    if (child.number != parent.number + 1) {
      return VerifyResult.fail('height not contiguous: parent=${parent.number}, child=${child.number}');
    }
    if (child.timestamp < parent.timestamp) {
      return VerifyResult.fail('timestamp not monotonic: parent=${parent.timestamp}, child=${child.timestamp}');
    }
    // Optional extra: if node provided hash, we can sanity check length
    if (!_looksLikeHash(child.hash)) {
      return VerifyResult.fail('child.hash not present/invalid: ${child.hash}');
    }
    return VerifyResult.okMsg('linkage ok (#${parent.number} → #${child.number})');
  }

  /// Verify a contiguous chain [headers], starting after [trusted].
  /// Returns the first failure or OK if all link.
  VerifyResult verifyChain(LightHeader trusted, List<LightHeader> headers) {
    if (headers.isEmpty) return VerifyResult.okMsg('no headers to verify');
    // The first header must build on the trusted checkpoint.
    final first = headers.first;
    final r0 = verifyLinkage(trusted, first);
    if (!r0.ok) return r0;
    // Then pairwise
    for (var i = 0; i < headers.length - 1; i++) {
      final r = verifyLinkage(headers[i], headers[i + 1]);
      if (!r.ok) return r;
    }
    return VerifyResult.okMsg('chain ok (${headers.first.number}..${headers.last.number})');
  }

  // ----------------- DA proof checks -----------------

  /// Verify a binary Merkle proof where `proofRoot` should match [header.daRoot].
  /// [leaf] can be provided to override `proofLeaf` (e.g., you hashed some payload yourself).
  VerifyResult verifyDaProofAgainstHeader({
    required LightHeader header,
    required String proofRootHex,
    required int index,
    required List<String> siblingsHex,
    String? proofLeafHex,
    Uint8List? leafOverride,
  }) {
    final daRoot = header.daRoot;
    if (daRoot == null || daRoot.isEmpty) {
      return VerifyResult.fail('header lacks daRoot');
    }

    final root = _bytesFromHex(proofRootHex);
    final target = _bytesFromHex(daRoot);
    if (!_bytesEq(root, target)) {
      return VerifyResult.fail('proof root != header.daRoot');
    }

    final leaf = leafOverride ??
        (proofLeafHex != null ? _bytesFromHex(proofLeafHex) : null);
    if (leaf == null) {
      return VerifyResult.fail('no leaf provided to verify DA proof');
    }

    // Binary Merkle walk (sha256)
    Uint8List acc = _sha256(leaf);
    int idx = index;
    for (final s in siblingsHex) {
      final sib = _bytesFromHex(s);
      if ((idx & 1) == 1) {
        acc = _sha256(_concat(sib, acc)); // right child
      } else {
        acc = _sha256(_concat(acc, sib)); // left child
      }
      idx >>= 1;
    }

    if (_bytesEq(acc, root)) {
      return VerifyResult.okMsg('DA proof verified for header #${header.number}');
    }
    return VerifyResult.fail('DA proof did not reconstruct root');
  }

  // ----------------- Utilities -----------------

  void close() => rpc.close();
}

// ========== small helpers ==========

bool _looksLikeHash(String s) {
  final t = s.toLowerCase();
  return t.startsWith('0x') && (t.length == 66 || t.length == 34 || t.length == 98);
}

String _normHex(String s) {
  if (s.startsWith('0x') || s.startsWith('0X')) return '0x${s.substring(2).toLowerCase()}';
  return '0x${s.toLowerCase()}';
}

bool _hexEq(String a, String b) {
  final aa = _strip0x(a).toLowerCase();
  final bb = _strip0x(b).toLowerCase();
  // Normalize leading zeros
  final aaa = aa.replaceFirst(RegExp(r'^0+'), '');
  final bbb = bb.replaceFirst(RegExp(r'^0+'), '');
  return aaa == bbb;
}

String _strip0x(String s) => (s.startsWith('0x') || s.startsWith('0X')) ? s.substring(2) : s;

String _short(String? h) {
  if (h == null || h.isEmpty) return '';
  final t = _strip0x(h);
  if (t.length <= 8) return '0x$t';
  return '0x${t.substring(0, 6)}…${t.substring(t.length - 4)}';
}

Map<String, dynamic> _coerceMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, v) => MapEntry(k.toString(), v));
  throw FormatException('Expected object map for header; got ${v.runtimeType}');
}

int _asInt(dynamic v) {
  if (v == null) return 0;
  if (v is int) return v;
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    return s.length <= 2 ? 0 : int.parse(s.substring(2), radix: 16);
  }
  return int.tryParse(s) ?? 0;
}

Uint8List _sha256(Uint8List data) {
  final d = sha256.convert(data).bytes;
  return Uint8List.fromList(d);
}

Uint8List _concat(Uint8List a, Uint8List b) {
  final out = Uint8List(a.length + b.length);
  out.setAll(0, a);
  out.setAll(a.length, b);
  return out;
}

bool _bytesEq(Uint8List a, Uint8List b) {
  if (a.length != b.length) return false;
  var x = 0;
  for (var i = 0; i < a.length; i++) {
    x |= a[i] ^ b[i];
  }
  return x == 0;
}

Uint8List _bytesFromHex(String hex) {
  var s = _strip0x(hex);
  if (s.isEmpty) return Uint8List(0);
  if (s.length.isOdd) s = '0$s';
  final out = Uint8List(s.length ~/ 2);
  for (int i = 0; i < s.length; i += 2) {
    out[i ~/ 2] = int.parse(s.substring(i, i + 2), radix: 16);
  }
  return out;
}
