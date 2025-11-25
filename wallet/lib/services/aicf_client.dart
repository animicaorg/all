/*
 * Animica Wallet — AICF Client (AI / Quantum Jobs)
 *
 * Capabilities:
 *   • enqueue(AicfJob)             → AicfTicket
 *   • getStatus(jobId)             → AicfStatus?
 *   • getResult(jobId)             → AicfResult?
 *   • cancel(jobId)                → bool
 *   • wait(jobId, ...)             → AicfResult  (poll until finished/fails)
 *   • listCapabilities()           → Map (models, queues, limits)  [optional]
 *
 * Transport:
 *   - If --dart-define=AICF_URL is set → use REST:
 *       POST /jobs               {kind, task, params, payloadB64?, payloadUri?, contentType?, model?, queue?, priority?, sigAlg?, sigHex?}
 *       GET  /jobs/{id}/status   → {id, state, progress?, position?, node?, error?, startedAt?, updatedAt?}
 *       GET  /jobs/{id}/result   → {id, state, contentType?, text?|json?|bytesB64?, computeMs?, cost?, logs?, model?}
 *       DELETE /jobs/{id}        → {ok:true}
 *       GET  /capabilities       → {models:[], queues:[], limits:{...}}
 *
 *   - Else fallback to JSON-RPC (env.rpcHttp):
 *       animica_aicf_enqueue(payload)            → {id, acceptedAt, position?}
 *       animica_aicf_status(id)                  → {...status...}
 *       animica_aicf_result(id)                  → {...result...}
 *       animica_aicf_cancel(id)                  → true
 *       animica_aicf_capabilities()              → {...}
 *
 * Signing (optional):
 *   - Provide `sign` to enqueue() to attach a signature over SHA-256 of a
 *     canonical JSON envelope plus payload bytes.
 *
 * Notes:
 *   - States: queued | running | succeeded | failed | canceled
 *   - This client is intentionally tolerant to slightly different field names.
 */

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:crypto/crypto.dart' show sha256;

import 'env.dart';
import 'rpc_client.dart';

typedef AicfSign = FutureOr<String> Function(Uint8List digest); // returns hex "0x…"

class AicfJob {
  final String kind;            // 'ai' | 'quantum' (free-form allowed)
  final String task;            // e.g. 'text.generate' | 'image.upscale' | 'qpu.run'
  final Map<String, dynamic> params;
  final Uint8List? payload;     // optional input bytes (models can ignore)
  final String? payloadUri;     // optional remote URI instead of payload
  final String? contentType;    // e.g. 'application/json', 'image/png', 'text/plain'
  final String? model;          // model/device name
  final String? queue;          // queue/region
  final int priority;           // higher = sooner (implementation-defined)
  final String? notes;          // human note (for logs/ops)

  const AicfJob({
    required this.kind,
    required this.task,
    required this.params,
    this.payload,
    this.payloadUri,
    this.contentType,
    this.model,
    this.queue,
    this.priority = 0,
    this.notes,
  });

  Map<String, dynamic> toCanonicalJson({bool forSigning = false}) {
    // Minimal canonicalization: stable key order by building a new map.
    final m = <String, dynamic>{
      'kind': kind,
      'task': task,
      'params': params,
      if (payloadUri != null) 'payloadUri': payloadUri,
      if (contentType != null) 'contentType': contentType,
      if (model != null) 'model': model,
      if (queue != null) 'queue': queue,
      'priority': priority,
      if (notes != null && !forSigning) 'notes': notes,
      'userAgent': env.userAgent,
    };
    return m;
  }
}

class AicfTicket {
  final String id;
  final DateTime? acceptedAt;
  final int? position;

  const AicfTicket({required this.id, this.acceptedAt, this.position});

  factory AicfTicket.fromJson(Map<String, dynamic> m) {
    return AicfTicket(
      id: (m['id'] ?? m['jobId'] ?? m['ticket'] ?? '').toString(),
      acceptedAt: _parseIso(m['acceptedAt']),
      position: _asIntOrNull(m['position']),
    );
  }
}

class AicfStatus {
  final String id;
  final String state;         // queued | running | succeeded | failed | canceled
  final double? progress;     // 0..1
  final int? position;        // queue position if queued
  final String? node;         // worker id
  final String? error;
  final DateTime? startedAt;
  final DateTime? updatedAt;

  const AicfStatus({
    required this.id,
    required this.state,
    this.progress,
    this.position,
    this.node,
    this.error,
    this.startedAt,
    this.updatedAt,
  });

  bool get done => state == 'succeeded' || state == 'failed' || state == 'canceled';

  factory AicfStatus.fromJson(Map<String, dynamic> m) {
    return AicfStatus(
      id: (m['id'] ?? m['jobId'] ?? '').toString(),
      state: (m['state'] ?? m['status'] ?? 'unknown').toString(),
      progress: _asDoubleOrNull(m['progress']),
      position: _asIntOrNull(m['position']),
      node: m['node']?.toString(),
      error: m['error']?.toString(),
      startedAt: _parseIso(m['startedAt']),
      updatedAt: _parseIso(m['updatedAt']),
    );
  }
}

class AicfResult {
  final String id;
  final String state;             // succeeded | failed | canceled
  final String? contentType;      // 'application/json', 'text/plain', 'image/png', ...
  final String? text;             // text output (if any)
  final Map<String, dynamic>? json;
  final Uint8List? bytes;         // raw bytes (if binary)
  final int? computeMs;
  final double? cost;             // units or $
  final String? logs;
  final String? model;

  const AicfResult({
    required this.id,
    required this.state,
    this.contentType,
    this.text,
    this.json,
    this.bytes,
    this.computeMs,
    this.cost,
    this.logs,
    this.model,
  });

  factory AicfResult.fromJson(Map<String, dynamic> m) {
    Uint8List? bytes;
    final b64 = m['bytesB64'] ?? m['b64'] ?? m['dataB64'];
    if (b64 is String) {
      try { bytes = base64.decode(b64); } catch (_) {}
    }
    return AicfResult(
      id: (m['id'] ?? m['jobId'] ?? '').toString(),
      state: (m['state'] ?? m['status'] ?? 'unknown').toString(),
      contentType: m['contentType']?.toString(),
      text: m['text']?.toString(),
      json: (m['json'] is Map<String, dynamic>)
          ? (m['json'] as Map<String, dynamic>)
          : (m['json'] is Map ? (m['json'] as Map).map((k, v) => MapEntry(k.toString(), v)) : null),
      bytes: bytes,
      computeMs: _asIntOrNull(m['computeMs']),
      cost: _asDoubleOrNull(m['cost']),
      logs: m['logs']?.toString(),
      model: m['model']?.toString(),
    );
  }
}

class AicfClient {
  final RpcClient _rpc;
  final http.Client _http;
  final Uri? _restBase;

  AicfClient._(this._rpc, this._http, this._restBase);

  factory AicfClient.fromEnv() {
    final rpc = RpcClient.fromEnv();
    final httpc = http.Client();
    final rest = _readAicfUrl();
    return AicfClient._(rpc, httpc, rest);
  }

  // ------------ Public API ------------

  /// Enqueue a job. If [sign] provided, a SHA-256 digest is produced and
  /// passed to it; the returned hex signature is attached (sigAlg defaults to Dilithium3).
  Future<AicfTicket> enqueue(
    AicfJob job, {
    AicfSign? sign,
    String sigAlg = 'dilithium3',
  }) async {
    final envJson = job.toCanonicalJson(forSigning: true);
    final payloadBytes = job.payload;

    // Build signing digest: sha256( utf8(json(envJson)) || payload? )
    String? sigHex;
    if (sign != null) {
      final canon = utf8.encode(jsonEncode(envJson));
      final sink = BytesBuilder(copy: false)..add(canon);
      if (payloadBytes != null) sink.add(payloadBytes);
      final digest = sha256.convert(sink.toBytes()).bytes;
      sigHex = await sign(Uint8List.fromList(digest));
      if (!sigHex.startsWith('0x')) sigHex = '0x$sigHex';
    }

    if (_restBase != null) {
      final req = {
        ...job.toCanonicalJson(),
        if (job.payloadUri != null) 'payloadUri': job.payloadUri,
        if (payloadBytes != null) 'payloadB64': base64.encode(payloadBytes),
        if (sigHex != null) 'sigAlg': sigAlg,
        if (sigHex != null) 'sigHex': sigHex,
      };
      final r = await _http.post(
        _restBase!.resolve('/jobs'),
        headers: _jsonHeaders(),
        body: jsonEncode(req),
      ).timeout(const Duration(seconds: 30));

      _ensure2xx(r);
      final m = _decodeJsonMap(r.body);
      return AicfTicket.fromJson(m);
    }

    // JSON-RPC path
    final req = {
      ...job.toCanonicalJson(),
      if (job.payloadUri != null) 'payloadUri': job.payloadUri,
      if (payloadBytes != null) 'payload': _hex0x(payloadBytes),
      if (sigHex != null) 'sigAlg': sigAlg,
      if (sigHex != null) 'sigHex': sigHex,
    };
    final res = await _rpc.call<dynamic>('animica_aicf_enqueue', [req]);
    return AicfTicket.fromJson(_coerceMap(res));
  }

  /// Fetch status; returns null if job not found.
  Future<AicfStatus?> getStatus(String jobId) async {
    if (_restBase != null) {
      final r = await _http.get(_restBase!.resolve('/jobs/$jobId/status'))
          .timeout(const Duration(seconds: 20));
      if (r.statusCode == 404) return null;
      _ensure2xx(r);
      return AicfStatus.fromJson(_decodeJsonMap(r.body));
    }

    final res = await _rpc.call<dynamic>('animica_aicf_status', [jobId]);
    if (res == null) return null;
    return AicfStatus.fromJson(_coerceMap(res));
  }

  /// Fetch result; returns null if not ready or not found.
  Future<AicfResult?> getResult(String jobId) async {
    if (_restBase != null) {
      final r = await _http.get(_restBase!.resolve('/jobs/$jobId/result'))
          .timeout(const Duration(seconds: 30));
      if (r.statusCode == 404) return null;
      _ensure2xx(r);
      return AicfResult.fromJson(_decodeJsonMap(r.body));
    }

    final res = await _rpc.call<dynamic>('animica_aicf_result', [jobId]);
    if (res == null) return null;
    return AicfResult.fromJson(_coerceMap(res));
  }

  /// Cancel a job; returns true if cancel request was accepted.
  Future<bool> cancel(String jobId) async {
    if (_restBase != null) {
      final r = await _http.delete(_restBase!.resolve('/jobs/$jobId'))
          .timeout(const Duration(seconds: 15));
      if (r.statusCode == 404) return false;
      _ensure2xx(r);
      final m = _decodeJsonMap(r.body);
      return (m['ok'] == true) || (m['status']?.toString() == 'canceled');
    }

    final res = await _rpc.call<dynamic>('animica_aicf_cancel', [jobId]);
    if (res is bool) return res;
    return res?.toString() == 'true';
  }

  /// Poll until the job reaches a terminal state. Returns the final result.
  /// Throws [TimeoutException] if not done within the [timeout].
  Future<AicfResult> wait(
    String jobId, {
    Duration timeout = const Duration(minutes: 5),
    Duration pollEvery = const Duration(seconds: 2),
    void Function(AicfStatus s)? onStatus,
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (true) {
      final s = await getStatus(jobId);
      if (s != null) onStatus?.call(s);
      if (s != null && s.done) {
        final r = await getResult(jobId);
        if (r == null) {
          // If failed/canceled may still return a result object with logs/error.
          return AicfResult(
            id: s.id,
            state: s.state,
            text: s.error,
            logs: s.error,
          );
        }
        return r;
      }
      if (DateTime.now().isAfter(deadline)) {
        throw TimeoutException('Timed out waiting for job $jobId');
      }
      await Future.delayed(pollEvery);
    }
  }

  /// Optional discovery endpoint.
  Future<Map<String, dynamic>> listCapabilities() async {
    if (_restBase != null) {
      final r = await _http.get(_restBase!.resolve('/capabilities'))
          .timeout(const Duration(seconds: 20));
      _ensure2xx(r);
      return _decodeJsonMap(r.body);
    }
    final res = await _rpc.call<dynamic>('animica_aicf_capabilities');
    return _coerceMap(res);
  }

  void close() {
    _http.close();
    _rpc.close();
  }

  // ------------ helpers ------------

  static Uri? _readAicfUrl() {
    final raw = const String.fromEnvironment('AICF_URL', defaultValue: '');
    if (raw.isEmpty) return null;
    return Uri.parse(raw);
  }

  static Map<String, String> _jsonHeaders() => {
        'content-type': 'application/json',
        'accept': 'application/json',
        'user-agent': env.userAgent,
      };

  static void _ensure2xx(http.Response r) {
    if (r.statusCode < 200 || r.statusCode >= 300) {
      throw Exception('AICF HTTP ${r.statusCode}: ${r.reasonPhrase ?? ''}');
    }
  }

  static Map<String, dynamic> _decodeJsonMap(String body) {
    final v = jsonDecode(body);
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return v.map((k, v) => MapEntry(k.toString(), v));
    throw FormatException('Expected JSON object');
  }

  static Map<String, dynamic> _coerceMap(dynamic v) {
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return v.map((k, v) => MapEntry(k.toString(), v));
    if (v is List && v.isNotEmpty && v.first is Map) {
      return _coerceMap(v.first);
    }
    throw FormatException('Expected object map; got ${v.runtimeType}');
  }

  static String _hex0x(Uint8List b) {
    final sb = StringBuffer('0x');
    for (final x in b) {
      if (x < 16) sb.write('0');
      sb.write(x.toRadixString(16));
    }
    return sb.toString();
  }
}

// ------------- value parsers -------------

DateTime? _parseIso(dynamic v) {
  if (v == null) return null;
  final s = v.toString();
  try {
    return DateTime.parse(s).toUtc();
  } catch (_) {
    return null;
  }
}

int? _asIntOrNull(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    return s.length <= 2 ? 0 : int.tryParse(s.substring(2), radix: 16);
  }
  return int.tryParse(s);
}

double? _asDoubleOrNull(dynamic v) {
  if (v == null) return null;
  if (v is num) return v.toDouble();
  return double.tryParse(v.toString());
}
