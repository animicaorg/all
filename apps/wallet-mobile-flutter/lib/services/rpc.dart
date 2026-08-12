// JSON-RPC client for Animica with multi-endpoint failover.
//
// Tries endpoints in order and remembers the last-good one for the
// remainder of the session so a single flap on the primary doesn't make
// every subsequent call pay the failover cost.

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../constants.dart';

class RpcError implements Exception {
  final int code;
  final String message;
  RpcError(this.code, this.message);
  @override
  String toString() => 'RpcError($code): $message';
}

class RpcClient {
  final List<String> endpoints;
  final http.Client _http;
  int _id = 0;
  int _preferredIdx = 0;
  final Duration _timeout;

  /// Transport-level circuit breaker: an endpoint that timed out or refused
  /// a connection is skipped for [_deadCooldown] so ONE dead host (e.g. a
  /// stale DNS record) costs a single stall per cooldown window instead of
  /// stalling every call — the exact failure mode that made sends "error
  /// out" when the old primary pointed at a decommissioned box. RPC-level
  /// errors never trip it: they come from a healthy node.
  final Map<String, DateTime> _deadUntil = {};
  static const Duration _deadCooldown = Duration(seconds: 90);

  RpcClient({
    List<String>? endpoints,
    http.Client? httpClient,
    Duration timeout = const Duration(seconds: 8),
  })  : endpoints = endpoints ?? AnimicaConfig.rpcEndpoints,
        _http = httpClient ?? http.Client(),
        _timeout = timeout;

  Future<dynamic> call(String method, [dynamic params]) async {
    _id++;
    final body = jsonEncode({
      'jsonrpc': '2.0',
      'id': _id,
      'method': method,
      if (params != null) 'params': params,
    });

    var order = _orderedEndpoints();
    // Skip endpoints inside their dead-cooldown — unless that would leave
    // nothing to try, in which case try everything (never fail without at
    // least one real attempt).
    final now = DateTime.now();
    final alive = order
        .where((ep) => !(_deadUntil[ep]?.isAfter(now) ?? false))
        .toList();
    if (alive.isNotEmpty) order = alive;

    final errors = <String>[];
    for (var i = 0; i < order.length; i++) {
      final ep = order[i];
      try {
        final resp = await _http
            .post(
              Uri.parse(ep),
              headers: const {'Content-Type': 'application/json'},
              body: body,
            )
            .timeout(_timeout);
        if (resp.statusCode != 200) {
          errors.add('$ep: http ${resp.statusCode}');
          continue;
        }
        final j = jsonDecode(resp.body) as Map<String, dynamic>;
        if (j.containsKey('error')) {
          final e = j['error'] as Map<String, dynamic>;
          // RPC-level errors are NOT transport failures — they came
          // from a healthy node. Surface them immediately rather than
          // failing over (which would just produce the same answer).
          _deadUntil.remove(ep);
          _preferredIdx = endpoints.indexOf(ep);
          throw RpcError(
            e['code'] as int? ?? -32603,
            e['message'] as String? ?? 'rpc error',
          );
        }
        _deadUntil.remove(ep);
        _preferredIdx = endpoints.indexOf(ep);
        return j['result'];
      } on TimeoutException {
        _deadUntil[ep] = DateTime.now().add(_deadCooldown);
        errors.add('$ep: timeout');
      } catch (e) {
        // Network errors are retryable.
        if (e is RpcError) rethrow;
        _deadUntil[ep] = DateTime.now().add(_deadCooldown);
        errors.add('$ep: $e');
      }
    }
    throw RpcError(
      -32603,
      'Could not reach the Animica network (${errors.join('; ')}). '
      'Check your connection and try again.',
    );
  }

  List<String> _orderedEndpoints() {
    if (_preferredIdx <= 0 || _preferredIdx >= endpoints.length) {
      return endpoints;
    }
    return [
      endpoints[_preferredIdx],
      ...endpoints.where((e) => e != endpoints[_preferredIdx]),
    ];
  }

  // ── high-level helpers ─────────────────────────────────────────────

  Future<int> chainHead() async {
    final r = await call('chain.getHead', {});
    if (r is Map && r['height'] is int) return r['height'] as int;
    return 0;
  }

  // NOTE: there is deliberately no `chainId()` helper here any more.
  //
  // The old one swallowed every error and fell back to the compile-time
  // `AnimicaConfig.chainId`, which made it impossible to tell "the node says
  // chain 1" from "the node said nothing". Since the chain id is signed into
  // every transaction preimage, a guess there is a wrong signature. The single
  // source of truth is `signer.dart:chainContextFor()`, which reads the full
  // chain identity, refuses a node on a different chain than this build, and
  // caches the result per client.

  Future<BigInt> getBalance(String address) async {
    final r = await call('state.getBalance', {'address': address});
    return _parseUintLike(r);
  }

  Future<int> getNonce(String address) async {
    final r = await call('state.getNonce', {'address': address});
    if (r is int) return r;
    if (r is String) return int.tryParse(r) ?? 0;
    return 0;
  }

  Future<int> getPendingNonce(String address) async {
    try {
      final r = await call('state.getPendingNonce', {'address': address});
      if (r is int) return r;
      if (r is String) return int.tryParse(r) ?? 0;
    } on RpcError {
      // Node version may not expose pending-nonce; fall back to latest.
    }
    return getNonce(address);
  }

  /// Returns the raw bytes of `to`'s contract view value, or null on
  /// `contract not found` / decode failure.
  Future<Uint8List?> stateCall({
    required String contract,
    required Uint8List data,
    String? from,
  }) async {
    try {
      final r = await call('state.call', {
        'to': contract,
        'data': '0x${_hex(data)}',
        if (from != null) 'from': from,
      });
      if (r is String) {
        var s = r;
        if (s.startsWith('0x') || s.startsWith('0X')) s = s.substring(2);
        final out = Uint8List(s.length ~/ 2);
        for (var i = 0; i < out.length; i++) {
          out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
        }
        return out;
      }
    } on RpcError catch (e) {
      if (e.message.toLowerCase().contains('contract not found')) return null;
      rethrow;
    }
    return null;
  }

  /// Submit a raw signed tx (CBOR-encoded envelope) and return the tx hash.
  Future<String> sendRawTransaction(Uint8List rawTx) async {
    final r = await call('tx.sendRawTransaction', ['0x${_hex(rawTx)}']);
    if (r is String) return r;
    if (r is Map && r['hash'] is String) return r['hash'] as String;
    if (r is Map && r['txid'] is String) return r['txid'] as String;
    throw RpcError(-32603, 'unexpected sendRawTransaction result shape');
  }

  Future<Map<String, dynamic>?> txReceipt(String txHash) async {
    try {
      final r = await call('tx.getReceipt', [txHash]);
      return r is Map<String, dynamic> ? r : null;
    } on RpcError {
      return null;
    }
  }

  Future<Map<String, dynamic>?> txStatus(String txHash) async {
    try {
      final r = await call('tx.getStatus', [txHash]);
      return r is Map<String, dynamic> ? r : null;
    } on RpcError {
      return null;
    }
  }

  void close() => _http.close();
}

BigInt _parseUintLike(dynamic v) {
  if (v == null) return BigInt.zero;
  if (v is int) return BigInt.from(v);
  if (v is BigInt) return v;
  if (v is String) {
    if (v.startsWith('0x') || v.startsWith('0X')) {
      return BigInt.parse(v.substring(2), radix: 16);
    }
    return BigInt.parse(v);
  }
  if (v is Map) {
    return _parseUintLike(v['confirmed'] ?? v['available'] ?? v['balance']);
  }
  return BigInt.zero;
}

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

String formatAnm(BigInt nanos, {int maxDecimals = 4}) {
  if (nanos == BigInt.zero) return '0';
  final whole = nanos ~/ AnimicaConfig.nanosPerAnm;
  final fracBig = nanos % AnimicaConfig.nanosPerAnm;
  if (fracBig == BigInt.zero) return whole.toString();
  var frac = fracBig.toString().padLeft(9, '0');
  if (frac.length > maxDecimals) frac = frac.substring(0, maxDecimals);
  frac = frac.replaceAll(RegExp(r'0+$'), '');
  return frac.isEmpty ? whole.toString() : '$whole.$frac';
}
