// Thin JSON-RPC client for Animica.
//
// Mirrors the public RPC surface used by the Chrome extension + the
// explorer: chain.getHead, state.getBalance, tx.getReceipt, etc.
//
// Notes on calling convention:
//   - Most state.* methods take a named-args object: {"address": "anim1…"}.
//   - chain.getBlockByNumber takes a positional array: [height, includeTxs,
//     includeReceipts].
//   - tx.sendRawTransaction is the broadcast endpoint, but Animica's tx
//     envelope is non-trivial to build in Dart without the executor's
//     canonical sign-bytes encoder. v0.1 of the mobile wallet treats
//     "send" as out-of-scope — see TODO in screens/send.dart.

import 'dart:convert';
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
  final String url;
  final http.Client _http;
  int _id = 0;

  RpcClient({String? url, http.Client? httpClient})
      : url = url ?? AnimicaConfig.rpcUrl,
        _http = httpClient ?? http.Client();

  Future<dynamic> call(String method, [dynamic params]) async {
    _id++;
    final body = jsonEncode({
      'jsonrpc': '2.0',
      'id': _id,
      'method': method,
      // Send params only when given; some methods (chain.getHead) take none.
      if (params != null) 'params': params,
    });
    final resp = await _http.post(
      Uri.parse(url),
      headers: const {'Content-Type': 'application/json'},
      body: body,
    );
    if (resp.statusCode != 200) {
      throw RpcError(-32603, 'http ${resp.statusCode}: ${resp.body}');
    }
    final j = jsonDecode(resp.body) as Map<String, dynamic>;
    if (j.containsKey('error')) {
      final e = j['error'] as Map<String, dynamic>;
      throw RpcError(e['code'] as int? ?? -32603, e['message'] as String? ?? 'rpc error');
    }
    return j['result'];
  }

  // ── convenience wrappers ───────────────────────────────────────────

  Future<int> chainHead() async {
    final r = await call('chain.getHead', {});
    if (r is Map && r['height'] is int) return r['height'] as int;
    return 0;
  }

  /// Returns balance as nano-ANM (BigInt).
  Future<BigInt> getBalance(String address) async {
    final r = await call('state.getBalance', {'address': address});
    // RPC may return a string, an int, or an object with `.confirmed` etc.
    BigInt _parse(dynamic v) {
      if (v == null) return BigInt.zero;
      if (v is int) return BigInt.from(v);
      if (v is BigInt) return v;
      if (v is String) {
        if (v.startsWith('0x') || v.startsWith('0X')) {
          return BigInt.parse(v.substring(2), radix: 16);
        }
        return BigInt.parse(v);
      }
      return BigInt.zero;
    }

    if (r is Map) {
      return _parse(r['confirmed'] ?? r['available'] ?? r['balance']);
    }
    return _parse(r);
  }

  Future<int> getNonce(String address) async {
    final r = await call('state.getNonce', {'address': address});
    if (r is int) return r;
    if (r is String) return int.tryParse(r) ?? 0;
    return 0;
  }

  Future<Map<String, dynamic>?> txReceipt(String txHash) async {
    try {
      final r = await call('tx.getReceipt', [txHash]);
      return r is Map<String, dynamic> ? r : null;
    } on RpcError {
      return null;
    }
  }

  void close() => _http.close();
}

/// Format a nano-ANM bigint as a human-readable ANM string with up to
/// 9 decimal places, trimming trailing zeros.
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
