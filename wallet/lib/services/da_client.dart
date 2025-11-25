/*
 * Animica Wallet — DA (Data Availability) Client
 *
 * Capabilities (best-effort, optional feature):
 *   • putBlob(bytes [, namespace, pin])  → DaPutResult {commitment, size, ...}
 *   • getBlob(commitment)                → Uint8List? (blob bytes)
 *   • getProof(commitment)               → DaProof? (shape is tolerant)
 *   • verifyProof(proof)                 → bool (simple binary Merkle check)
 *
 * Transports supported:
 *   1) JSON-RPC (default; uses env.rpcHttp)
 *      Methods (conventional Animica naming; adjust if your node differs):
 *        - animica_da_putBlob(nsHex, dataHex, {pin:bool?}) → {commitment:0x.., size:int, ...}
 *        - animica_da_getBlob(commitmentHex) → 0x… or base64 or {data:…}
 *        - animica_da_getBlobProof(commitmentHex) → {root:0x.., index:int, siblings:[0x..], leaf:0x..}
 *
 *   2) REST (if --dart-define=DA_URL=<https://da.service>)
 *        POST  /put   {namespace,data,pin?}  → {...like above...}
 *        GET   /blob/{commitment}            → bytes (or JSON with data field)
 *        GET   /proof/{commitment}           → {...proof...}
 *
 * Notes:
 *   • Proof verification here implements a *simple binary Merkle* strategy
 *     with sha256 for hashing. If your network uses NMT/other hashing, treat
 *     this as a hint and rely on the node’s verification or a dedicated lib.
 */

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'rpc_client.dart';
import 'env.dart';

const _USE_REST_DEFAULT = false; // flipped to true if DA_URL is provided

class DaPutResult {
  final String commitment; // 0x…
  final String? namespace; // 0x… (optional)
  final int size;          // bytes
  final int? height;
  final int? index;

  DaPutResult({
    required this.commitment,
    required this.size,
    this.namespace,
    this.height,
    this.index,
  });

  factory DaPutResult.fromJson(Map<String, dynamic> m) {
    return DaPutResult(
      commitment: (m['commitment'] ?? m['cid'] ?? m['id'] ?? '').toString(),
      namespace: m['namespace']?.toString(),
      size: _asInt(m['size']),
      height: _asIntOrNull(m['height']),
      index: _asIntOrNull(m['index']),
    );
  }

  Map<String, dynamic> toJson() => {
        'commitment': commitment,
        if (namespace != null) 'namespace': namespace,
        'size': size,
        if (height != null) 'height': height,
        if (index != null) 'index': index,
      };
}

class DaProof {
  final String root;          // 0x…
  final int index;            // leaf index
  final List<String> siblings;// 0x… hashes (left/right order inferred)
  final String? leaf;         // 0x… (optional)
  final String? commitment;   // 0x… (optional)
  final String? namespace;    // 0x… (optional)
  final String? hashAlg;      // 'sha256' | 'keccak256' | 'nmt' (hint only)

  DaProof({
    required this.root,
    required this.index,
    required this.siblings,
    this.leaf,
    this.commitment,
    this.namespace,
    this.hashAlg,
  });

  factory DaProof.fromJson(Map<String, dynamic> m) {
    final sib = (m['siblings'] ?? m['path'] ?? m['proof'] ?? const []) as List;
    return DaProof(
      root: (m['root'] ?? m['merkleRoot'] ?? '').toString(),
      index: _asInt(m['index']),
      siblings: sib.map((e) => e.toString()).toList(),
      leaf: m['leaf']?.toString(),
      commitment: m['commitment']?.toString(),
      namespace: m['namespace']?.toString(),
      hashAlg: m['hashAlg']?.toString(),
    );
  }

  Map<String, dynamic> toJson() => {
        'root': root,
        'index': index,
        'siblings': siblings,
        if (leaf != null) 'leaf': leaf,
        if (commitment != null) 'commitment': commitment,
        if (namespace != null) 'namespace': namespace,
        if (hashAlg != null) 'hashAlg': hashAlg,
      };
}

class DaClient {
  final RpcClient _rpc;
  final Uri? _restBase;
  final http.Client _http;

  DaClient._rpc(this._rpc)
      : _restBase = _readDaUrl(),
        _http = http.Client();

  factory DaClient.fromEnv() => DaClient._rpc(RpcClient.fromEnv());

  /// Upload a blob to DA. If [namespace] provided, it should be 8–32 bytes.
  /// Returns commitment and metadata.
  Future<DaPutResult> putBlob(
    Uint8List data, {
    Uint8List? namespace,
    bool pin = false,
  }) async {
    final rest = _restBase;
    final nsHex = namespace != null ? _hex0x(namespace) : null;
    final dataHex = _hex0x(data);

    if (rest != null) {
      final url = rest.resolve('/put');
      final payload = {
        if (nsHex != null) 'namespace': nsHex,
        'data': dataHex,
        if (pin) 'pin': true,
      };
      final resp = await _http
          .post(url, headers: _jsonHeaders(), body: jsonEncode(payload))
          .timeout(const Duration(seconds: 30));
      _ensure2xx(resp);
      final m = _decodeJsonMap(resp.body);
      return DaPutResult.fromJson(m);
    }

    final res = await _rpc.call<dynamic>(
      'animica_da_putBlob',
      [
        nsHex ?? '0x',
        dataHex,
        if (pin) {'pin': true},
      ],
    );

    final Map<String, dynamic> m = _coerceMap(res);
    return DaPutResult.fromJson(m);
  }

  /// Fetch raw blob bytes by [commitment] (0x…).
  Future<Uint8List?> getBlob(String commitment) async {
    final rest = _restBase;
    if (rest != null) {
      final resp = await _http
          .get(rest.resolve('/blob/$commitment'))
          .timeout(const Duration(seconds: 30));
      if (resp.statusCode == 404) return null;
      _ensure2xx(resp);

      // Try raw bytes first (some DA services return application/octet-stream)
      final ct = resp.headers['content-type'] ?? '';
      if (!ct.contains('json')) {
        return Uint8List.fromList(resp.bodyBytes);
      }

      // Else JSON with data field (0x… or base64)
      final m = _decodeJsonMap(resp.body);
      final d = m['data'];
      if (d is String) {
        return _bytesFromAnyString(d);
      }
      return null;
    }

    final res = await _rpc.call<dynamic>('animica_da_getBlob', [commitment]);
    if (res == null) return null;
    if (res is String) {
      return _bytesFromAnyString(res);
    }
    if (res is Map) {
      final m = _coerceMap(res);
      final d = m['data'] ?? m['blob'];
      if (d is String) return _bytesFromAnyString(d);
      if (d is List<int>) return Uint8List.fromList(d);
    }
    // As a last resort, try to parse as base64 of JSON stringified
    try {
      return base64.decode(res.toString());
    } catch (_) {}
    return null;
  }

  /// Retrieve a Merkle/NMT proof object for [commitment].
  Future<DaProof?> getProof(String commitment) async {
    final rest = _restBase;
    if (rest != null) {
      final resp = await _http
          .get(rest.resolve('/proof/$commitment'))
          .timeout(const Duration(seconds: 30));
      if (resp.statusCode == 404) return null;
      _ensure2xx(resp);
      final m = _decodeJsonMap(resp.body);
      return DaProof.fromJson(m);
    }

    final res =
        await _rpc.call<dynamic>('animica_da_getBlobProof', [commitment]);
    if (res == null) return null;
    final m = _coerceMap(res);
    return DaProof.fromJson(m);
  }

  /// Best-effort verification using *binary Merkle with sha256*.
  /// If your network uses a different construction, this may return false
  /// even for valid proofs—treat as a local hint only.
  bool verifyProof(DaProof proof, {Uint8List? leafOverride}) {
    try {
      final root = _bytesFromHex(proof.root);
      final leaf = leafOverride ??
          (proof.leaf != null ? _bytesFromAnyString(proof.leaf!) : null);
      if (leaf == null) return false;

      Uint8List acc = _sha256(leaf);
      int idx = proof.index;

      for (final s in proof.siblings) {
        final sib = _bytesFromHex(s);
        if ((idx & 1) == 1) {
          // right child: hash(sibling || acc)
          acc = _sha256(_concat(sib, acc));
        } else {
          // left child: hash(acc || sibling)
          acc = _sha256(_concat(acc, sib));
        }
        idx >>= 1;
      }
      return _equalBytes(acc, root);
    } catch (_) {
      return false;
    }
  }

  // --------------- helpers ---------------

  static Map<String, String> _jsonHeaders() => {
        'content-type': 'application/json',
        'accept': 'application/json',
        'user-agent': env.userAgent,
      };

  static void _ensure2xx(http.Response r) {
    if (r.statusCode < 200 || r.statusCode >= 300) {
      throw Exception('DA HTTP ${r.statusCode}: ${r.reasonPhrase ?? ''}');
    }
  }

  static Map<String, dynamic> _decodeJsonMap(String body) {
    final v = jsonDecode(body);
    if (v is Map<String, dynamic>) return v;
    if (v is Map) {
      return v.map((k, v) => MapEntry(k.toString(), v));
    }
    throw FormatException('Expected JSON object for DA response');
  }

  static Map<String, dynamic> _coerceMap(dynamic v) {
    if (v is Map<String, dynamic>) return v;
    if (v is Map) {
      return v.map((k, v) => MapEntry(k.toString(), v));
    }
    // sometimes RPC returns array like [result]; unwrap if single
    if (v is List && v.isNotEmpty && v.first is Map) {
      return _coerceMap(v.first);
    }
    throw FormatException('Expected object map; got ${v.runtimeType}');
  }

  static Uint8List _bytesFromAnyString(String s) {
    final t = s.trim();
    if (t.startsWith('0x') || t.startsWith('0X')) {
      return _bytesFromHex(t);
    }
    // try base64
    try {
      return Uint8List.fromList(base64.decode(t));
    } catch (_) {
      // final fallback: utf8 bytes
      return Uint8List.fromList(utf8.encode(t));
    }
  }

  static String _hex0x(Uint8List b) {
    final sb = StringBuffer('0x');
    for (final x in b) {
      if (x < 16) sb.write('0');
      sb.write(x.toRadixString(16));
    }
    return sb.toString();
  }

  static Uint8List _bytesFromHex(String hex) {
    var s = hex.startsWith('0x') || hex.startsWith('0X') ? hex.substring(2) : hex;
    if (s.length.isOdd) s = '0$s';
    final out = Uint8List(s.length ~/ 2);
    for (int i = 0; i < s.length; i += 2) {
      out[i ~/ 2] = int.parse(s.substring(i, i + 2), radix: 16);
    }
    return out;
  }

  static Uint8List _concat(Uint8List a, Uint8List b) {
    final out = Uint8List(a.length + b.length);
    out.setAll(0, a);
    out.setAll(a.length, b);
    return out;
  }

  static bool _equalBytes(Uint8List a, Uint8List b) {
    if (a.length != b.length) return false;
    var acc = 0;
    for (var i = 0; i < a.length; i++) {
      acc |= a[i] ^ b[i];
    }
    return acc == 0;
  }

  // Simple SHA-256 (crypto is in pubspec)
  static Uint8List _sha256(Uint8List data) {
    final digest = sha256.convert(data); // from package:crypto/crypto.dart
    return Uint8List.fromList(digest.bytes);
  }

  static Uri? _readDaUrl() {
    final raw = const String.fromEnvironment('DA_URL', defaultValue: '');
    if (raw.isEmpty) return _USE_REST_DEFAULT ? Uri.parse('http://127.0.0.1:7788') : null;
    return Uri.parse(raw);
  }

  void close() {
    _http.close();
    _rpc.close();
  }
}

// -------- small number coercion helpers --------

int _asInt(dynamic v) {
  if (v == null) return 0;
  if (v is int) return v;
  if (v is String) {
    final s = v.trim();
    if (s.startsWith('0x') || s.startsWith('0X')) {
      return s.length <= 2 ? 0 : int.parse(s.substring(2), radix: 16);
    }
    return int.tryParse(s) ?? 0;
  }
  return int.tryParse(v.toString()) ?? 0;
}

int? _asIntOrNull(dynamic v) {
  if (v == null) return null;
  final n = _asInt(v);
  return n;
}

// NOTE: Import for sha256
import 'package:crypto/crypto.dart' show sha256;
