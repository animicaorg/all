/*
 * Animica Wallet — JSON-RPC HTTP client (retries + backoff)
 *
 * Features
 *  • Typed RpcException with code/message/data
 *  • Exponential backoff with jitter on transient errors
 *  • Batch calls
 *  • Pluggable headers (adds User-Agent from env by default)
 *  • Safe JSON encoder for BigInt/Uint8List/Uri
 *
 * Usage
 *   import 'env.dart';
 *   import 'rpc_client.dart';
 *
 *   initEnv(); // sets global `env`
 *   final rpc = RpcClient.fromEnv();
 *   final bn = await rpc.call<String>('animica_blockNumber');
 *
 * Notes
 *  • We DO NOT retry JSON-RPC business errors (i.e., valid HTTP 200 with "error"),
 *    only transport faults (network, 408/429/5xx) and decoding hiccups once.
 */

import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:http/http.dart' as http;

import 'env.dart';

/// JSON-RPC error wrapper.
class RpcException implements Exception {
  final int? code; // JSON-RPC error.code if present
  final String message;
  final dynamic data; // optional JSON-RPC error.data
  final int? httpStatus; // HTTP status if relevant
  final Uri endpoint;
  final String method;

  RpcException({
    required this.method,
    required this.endpoint,
    required this.message,
    this.code,
    this.data,
    this.httpStatus,
  });

  @override
  String toString() {
    final c = code != null ? ' code=$code' : '';
    final hs = httpStatus != null ? ' http=$httpStatus' : '';
    return 'RpcException($method@$endpoint:$hs$c msg="$message")';
  }
}

/// Simple request descriptor for batch calls.
class RpcCall {
  final String method;
  final dynamic params; // List or Map or null
  const RpcCall(this.method, [this.params]);
}

/// Minimal JSON encoder that is friendly to BigInt/Uint8List/Uri types.
class _SafeJsonEncoder extends Converter<Object?, Object?> {
  const _SafeJsonEncoder();

  @override
  Object? convert(Object? input) {
    if (input == null) return null;
    if (input is num || input is bool || input is String) return input;
    if (input is BigInt) return input.toString();
    if (input is Uint8List) return base64.encode(input);
    if (input is Uri) return input.toString();
    if (input is List) return input.map(convert).toList();
    if (input is Map) {
      return input.map((k, v) => MapEntry(k.toString(), convert(v)));
    }
    // Fallback to toString() to avoid encoder crashes.
    return input.toString();
  }
}

class RpcClient {
  final Uri endpoint;
  final http.Client _client;
  final Duration timeout;
  final int maxRetries;
  final Duration baseBackoff; // first retry delay
  final Map<String, String> defaultHeaders;

  int _idCtr = DateTime.now().microsecondsSinceEpoch & 0x7fffffff;
  final _jsonEncoder = const _SafeJsonEncoder();

  RpcClient({
    required this.endpoint,
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 20),
    this.maxRetries = 3,
    this.baseBackoff = const Duration(milliseconds: 350),
    Map<String, String>? defaultHeaders,
  })  : _client = httpClient ?? http.Client(),
        defaultHeaders = {
          'content-type': 'application/json',
          'accept': 'application/json',
          'user-agent': env.userAgent,
          ...?defaultHeaders,
        };

  /// Build a client targeting env.rpcHttp with UA from env.
  factory RpcClient.fromEnv() => RpcClient(endpoint: env.rpcHttp);

  /// Dispose underlying HTTP client if you created many instances.
  void close() => _client.close();

  /// Single JSON-RPC call. Returns the "result" field parsed as dynamic.
  Future<T> call<T>(String method, [dynamic params]) async {
    final payload = _singlePayload(method, params);
    final body = json.encode(payload, toEncodable: _jsonEncoder.convert);
    final res = await _sendWithRetry(body, isBatch: false, method: method);
    return res as T;
  }

  /// Batch JSON-RPC calls. Returns a list of results in the same order.
  Future<List<dynamic>> batch(List<RpcCall> calls) async {
    if (calls.isEmpty) return const [];
    final payload = <Map<String, dynamic>>[];
    for (final c in calls) {
      payload.add(_singlePayload(c.method, c.params));
    }
    final body = json.encode(payload, toEncodable: _jsonEncoder.convert);
    final res = await _sendWithRetry(body, isBatch: true, method: 'batch(${calls.length})');
    return (res as List).cast<dynamic>();
  }

  Map<String, dynamic> _singlePayload(String method, dynamic params) {
    final id = _nextId();
    final normalizedParams = (params == null)
        ? const []
        : (params is List || params is Map ? params : [params]);
    return <String, dynamic>{
      'jsonrpc': '2.0',
      'id': id,
      'method': method,
      'params': normalizedParams,
    };
  }

  int _nextId() => ++_idCtr;

  Future<dynamic> _sendWithRetry(String body, {required bool isBatch, required String method}) async {
    int attempt = 0;
    final maxA = maxRetries.clamp(0, 10);
    final rnd = Random.secure();

    while (true) {
      attempt += 1;
      try {
        if (kDebugMode) {
          // Avoid spamming large payloads; trim at ~2KB in debug.
          final preview = body.length > 2048 ? '${body.substring(0, 2048)}…' : body;
          // ignore: avoid_print
          print('[rpc] → $method attempt=$attempt ${isBatch ? '(batch)' : ''} ${endpoint} body=${preview.length}B');
        }

        final resp = await _client
            .post(
              endpoint,
              headers: defaultHeaders,
              body: body,
            )
            .timeout(timeout);

        if (resp.statusCode >= 200 && resp.statusCode < 300) {
          final parsed = json.decode(resp.body);
          if (isBatch) {
            if (parsed is! List) {
              throw RpcException(
                method: method,
                endpoint: endpoint,
                message: 'Expected batch response (array)',
                httpStatus: resp.statusCode,
              );
            }
            // Map id->result and return in request order:
            final byId = <int, dynamic>{};
            for (final item in parsed) {
              final id = item['id'];
              if (item['error'] != null) {
                final err = item['error'];
                throw RpcException(
                  method: 'batch-item',
                  endpoint: endpoint,
                  message: err['message']?.toString() ?? 'RPC error',
                  code: (err['code'] is int) ? err['code'] as int : null,
                  data: err['data'],
                );
              }
              byId[(id is int) ? id : int.tryParse(id.toString()) ?? -1] = item['result'];
            }
            // Since we used incremental ids when building, just collect by id ascending.
            final ids = byId.keys.toList()..sort();
            return ids.map((i) => byId[i]).toList();
          } else {
            if (parsed is! Map) {
              throw RpcException(
                method: method,
                endpoint: endpoint,
                message: 'Expected object response',
                httpStatus: resp.statusCode,
              );
            }
            if (parsed['error'] != null) {
              final err = parsed['error'];
              throw RpcException(
                method: method,
                endpoint: endpoint,
                message: err['message']?.toString() ?? 'RPC error',
                code: (err['code'] is int) ? err['code'] as int : null,
                data: err['data'],
                httpStatus: resp.statusCode,
              );
            }
            return parsed['result'];
          }
        }

        // HTTP non-2xx: decide retry vs throw.
        if (_isRetryableStatus(resp.statusCode)) {
          _maybeSleep(attempt, maxA, rnd);
          continue;
        }
        throw RpcException(
          method: method,
          endpoint: endpoint,
          message: 'HTTP ${resp.statusCode}: ${resp.reasonPhrase ?? ''}',
          httpStatus: resp.statusCode,
        );
      } on TimeoutException catch (e) {
        if (attempt <= maxA) {
          _maybeSleep(attempt, maxA, rnd);
          continue;
        }
        throw RpcException(
          method: method,
          endpoint: endpoint,
          message: 'Timeout: ${e.message ?? ''}',
        );
      } on FormatException catch (e) {
        // Malformed JSON: retry once (maybe a transient proxy/body cut).
        if (attempt <= 1 && maxA >= 1) {
          _maybeSleep(attempt, maxA, rnd);
          continue;
        }
        throw RpcException(
          method: method,
          endpoint: endpoint,
          message: 'Decode error: ${e.message}',
        );
      } on RpcException {
        rethrow; // already wrapped
      } catch (e) {
        // Network/IO errors
        if (attempt <= maxA) {
          _maybeSleep(attempt, maxA, rnd);
          continue;
        }
        throw RpcException(
          method: method,
          endpoint: endpoint,
          message: 'Network error: $e',
        );
      }
    }
  }

  bool _isRetryableStatus(int code) {
    // 408 Request Timeout, 429 Too Many Requests, 5xx server errors are retryable.
    if (code == 408 || code == 429) return true;
    if (code >= 500 && code <= 599) return true;
    return false;
  }

  void _maybeSleep(int attempt, int maxA, Random rnd) {
    final factor = pow(2, attempt - 1).toDouble();
    final baseMs = baseBackoff.inMilliseconds * factor;
    final jitter = rnd.nextInt(150); // up to +150ms
    final wait = Duration(milliseconds: baseMs.toInt() + jitter);
    if (kDebugMode) {
      // ignore: avoid_print
      print('[rpc] retrying attempt=$attempt/$maxA after ${wait.inMilliseconds}ms…');
    }
    // Sleep async
    // ignore: discarded_futures
    Future.delayed(wait);
  }
}
