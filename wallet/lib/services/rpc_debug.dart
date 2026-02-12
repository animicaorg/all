/*
 * Animica Wallet — RPC Debug Tracker
 *
 * Captures recent RPC calls for debugging balance issues.
 * Stores the last 20 calls in memory (ring buffer).
 */

import 'dart:collection';

class RpcDebugEntry {
  final DateTime timestamp;
  final String method;
  final dynamic params;
  final String rpcUrl;
  final dynamic result;
  final String? error;
  final int? errorCode;
  final Duration latency;

  const RpcDebugEntry({
    required this.timestamp,
    required this.method,
    required this.params,
    required this.rpcUrl,
    this.result,
    this.error,
    this.errorCode,
    required this.latency,
  });

  bool get isError => error != null;

  Map<String, dynamic> toJson() => {
    'timestamp': timestamp.toIso8601String(),
    'method': method,
    'params': params,
    'rpcUrl': rpcUrl,
    'result': result,
    'error': error,
    'errorCode': errorCode,
    'latencyMs': latency.inMilliseconds,
  };
}

class RpcDebugTracker {
  static final RpcDebugTracker instance = RpcDebugTracker._();
  RpcDebugTracker._();

  final Queue<RpcDebugEntry> _entries = Queue();
  static const int _maxEntries = 20;

  /// Add a new RPC call entry
  void track({
    required String method,
    required dynamic params,
    required String rpcUrl,
    dynamic result,
    String? error,
    int? errorCode,
    required Duration latency,
  }) {
    final entry = RpcDebugEntry(
      timestamp: DateTime.now().toUtc(),
      method: method,
      params: params,
      rpcUrl: rpcUrl,
      result: result,
      error: error,
      errorCode: errorCode,
      latency: latency,
    );

    _entries.addFirst(entry);
    if (_entries.length > _maxEntries) {
      _entries.removeLast();
    }
  }

  /// Get all tracked entries (most recent first)
  List<RpcDebugEntry> get entries => _entries.toList();

  /// Clear all entries
  void clear() => _entries.clear();

  /// Get summary statistics
  Map<String, dynamic> get stats {
    if (_entries.isEmpty) {
      return {
        'totalCalls': 0,
        'errors': 0,
        'avgLatencyMs': 0,
      };
    }

    final errors = _entries.where((e) => e.isError).length;
    final avgLatency = _entries
        .map((e) => e.latency.inMilliseconds)
        .reduce((a, b) => a + b) / _entries.length;

    return {
      'totalCalls': _entries.length,
      'errors': errors,
      'avgLatencyMs': avgLatency.toStringAsFixed(1),
    };
  }
}
