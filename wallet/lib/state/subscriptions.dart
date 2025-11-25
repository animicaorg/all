/*
 * Animica Wallet — Subscriptions (newHeads / pendingTxs)
 *
 * Exposes Riverpod StreamProviders for:
 *   • newHeadsStreamProvider        → emits ChainHead on each new header
 *   • pendingTxHashesProvider       → emits tx hash strings as they appear
 *
 * Strategy
 *   - Prefer WebSocket JSON-RPC subscriptions:
 *       * animica_subscribe("newHeads") or eth_subscribe("newHeads")
 *       * animica_subscribe("newPendingTransactions") or eth_subscribe("newPendingTransactions")
 *   - If WS isn’t available (or errors), we gracefully fall back to polling the
 *     HTTP RPC for new heads every 2s (pendingTx fallback is disabled by default).
 *
 * Notes
 *   - This file relies on WsClient having a `subscribe(method, params)` helper
 *     that returns a Stream of decoded JSON subscription messages. The provided
 *     ws_client.dart in this repo defines this surface. If yours differs, adapt
 *     the _wsSubscribe() function below.
 *   - We keep the logic self-contained and autoDispose streams on last listener.
 */

import 'dart:async';
import 'dart:math';

import 'package:riverpod/riverpod.dart';

import '../services/rpc_client.dart';
import '../services/ws_client.dart';
import 'providers.dart' show
  rpcClientProvider, wsClientProvider, wsUrlProvider;

/// Minimal header model for UI & consumers.
class ChainHead {
  final int number;         // block height/number
  final String? hash;       // hex hash if provided by node
  final DateTime seenAt;    // when we observed this head (local UTC)

  const ChainHead({required this.number, this.hash, required this.seenAt});

  @override
  String toString() => 'Head(#$number ${hash ?? ""})';
}

// ---------- Public providers ----------

/// Stream of new heads. Uses WS subscription if possible; otherwise polls.
final newHeadsStreamProvider =
    StreamProvider.autoDispose<ChainHead>((ref) async* {
  final wsUrl = ref.watch(wsUrlProvider);
  final rpc = ref.watch(rpcClientProvider);

  // Try WS first.
  if (wsUrl.trim().isNotEmpty) {
    final ws = ref.watch(wsClientProvider);
    final stream = await _newHeadsViaWs(ref, ws);
    if (stream != null) {
      yield* stream;
      return;
    }
  }

  // Fallback: HTTP polling every ~2s with jitter.
  yield* _newHeadsViaPolling(ref, rpc);
});

/// Stream of pending tx hashes (strings). WS only; no HTTP fallback.
final pendingTxHashesProvider =
    StreamProvider.autoDispose<String>((ref) async* {
  final wsUrl = ref.watch(wsUrlProvider);
  if (wsUrl.trim().isEmpty) {
    // No WS → silent (empty) stream.
    return;
  }
  final ws = ref.watch(wsClientProvider);
  final stream = await _pendingTxsViaWs(ref, ws);
  if (stream != null) {
    yield* stream;
  }
});

// ---------- WS implementations ----------

Future<Stream<ChainHead>?> _newHeadsViaWs(Ref ref, WsClient ws) async {
  // Ensure connected; WsClient handles auto-reconnect internally.
  try {
    await ws.connect();
  } catch (_) {
    return null;
  }

  // Try Animica then ETH method names.
  Stream<dynamic>? sub;
  try {
    sub = await ws.subscribe('animica_subscribe', ['newHeads']);
  } catch (_) {
    try {
      sub = await ws.subscribe('eth_subscribe', ['newHeads']);
    } catch (_) {
      sub = null;
    }
  }
  if (sub == null) return null;

  // Decode messages and yield ChainHead.
  final controller = StreamController<ChainHead>();
  final subCancel = sub.listen((msg) {
    try {
      final res = _extractSubscriptionResult(msg);
      if (res == null) return;
      final n = _asInt(res['number'] ?? res['height']);
      if (n == null) return;
      final hash = (res['hash'] ?? res['blockHash'])?.toString();
      controller.add(ChainHead(number: n, hash: hash, seenAt: DateTime.now().toUtc()));
    } catch (_) {
      // ignore bad frames
    }
  }, onError: (e, st) {
    // Bubble as done; upstream provider will recreate if there are listeners.
    controller.close();
  }, onDone: () {
    controller.close();
  });

  controller.onCancel = () async {
    await subCancel.cancel();
  };
  return controller.stream;
}

Future<Stream<String>?> _pendingTxsViaWs(Ref ref, WsClient ws) async {
  try {
    await ws.connect();
  } catch (_) {
    return null;
  }
  Stream<dynamic>? sub;
  try {
    sub = await ws.subscribe('animica_subscribe', ['newPendingTransactions']);
  } catch (_) {
    try {
      sub = await ws.subscribe('eth_subscribe', ['newPendingTransactions']);
    } catch (_) {
      sub = null;
    }
  }
  if (sub == null) return null;

  final controller = StreamController<String>();
  final subCancel = sub.listen((msg) {
    try {
      final res = _extractSubscriptionResult(msg);
      if (res == null) {
        // Some nodes emit directly as a string hash in `result`.
        final h = _extractSubscriptionString(msg);
        if (h != null) controller.add(_normHex(h));
        return;
      }
      // Many nodes just send the hash string as the "result"
      if (res is String) {
        controller.add(_normHex(res));
        return;
      }
      // Or inside a map
      final maybeHash = (res['hash'] ?? res['txHash'] ?? res['transactionHash']);
      if (maybeHash != null) controller.add(_normHex(maybeHash.toString()));
    } catch (_) {
      // ignore bad frames
    }
  }, onError: (e, st) {
    controller.close();
  }, onDone: () {
    controller.close();
  });

  controller.onCancel = () async {
    await subCancel.cancel();
  };
  return controller.stream;
}

// ---------- HTTP polling fallback ----------

Stream<ChainHead> _newHeadsViaPolling(Ref ref, RpcClient rpc) async* {
  int last = -1;
  final rnd = Random();
  while (ref.mounted) {
    try {
      final n = await _fetchBlockNumber(rpc);
      if (n != null && n >= 0 && n != last) {
        last = n;
        String? hash;
        // Best-effort fetch of hash
        try {
          final hdr = await rpc.call<dynamic>('animica_getHeaderByNumber', ['latest']);
          if (hdr is Map && hdr['hash'] != null) {
            hash = hdr['hash'].toString();
          }
        } catch (_) {
          try {
            final blk = await rpc.call<dynamic>('eth_getBlockByNumber', ['latest', false]);
            if (blk is Map && blk['hash'] != null) hash = blk['hash'].toString();
          } catch (_) {}
        }
        yield ChainHead(number: n, hash: hash, seenAt: DateTime.now().toUtc());
      }
    } catch (_) {
      // Ignore and keep polling.
    }
    // 2s ±20% jitter
    final baseMs = 2000;
    final jitter = (rnd.nextDouble() * baseMs * 0.2).toInt();
    final sign = rnd.nextBool() ? 1 : -1;
    final waitMs = (baseMs + sign * jitter).clamp(800, 5000);
    await Future.delayed(Duration(milliseconds: waitMs));
  }
}

// ---------- Helpers ----------

Future<int?> _fetchBlockNumber(RpcClient rpc) async {
  // Animica preferred
  try {
    final v = await rpc.call<dynamic>('animica_blockNumber');
    final n = _asInt(v);
    if (n != null) return n;
  } catch (_) {}
  // ETH fallback
  try {
    final v = await rpc.call<dynamic>('eth_blockNumber');
    final n = _asInt(v);
    if (n != null) return n;
  } catch (_) {}
  // Fallback via latest header
  try {
    final hdr = await rpc.call<dynamic>('animica_getHeaderByNumber', ['latest']);
    if (hdr is Map) return _asInt(hdr['number'] ?? hdr['height']);
  } catch (_) {}
  try {
    final blk = await rpc.call<dynamic>('eth_getBlockByNumber', ['latest', false]);
    if (blk is Map) return _asInt(blk['number']);
  } catch (_) {}
  return null;
}

dynamic _extractSubscriptionResult(dynamic msg) {
  // Common shapes:
  // { "method":"eth_subscription","params":{"subscription":"0x..","result":{...}} }
  // Some nodes may emit {"result":{...}} directly.
  if (msg is Map) {
    if (msg['params'] is Map) {
      final p = msg['params'] as Map;
      if (p.containsKey('result')) return p['result'];
    }
    if (msg.containsKey('result')) return msg['result'];
  }
  return null;
}

String? _extractSubscriptionString(dynamic msg) {
  if (msg is String) return msg;
  if (msg is Map) {
    if (msg['params'] is Map) {
      final p = msg['params'] as Map;
      final r = p['result'];
      if (r is String) return r;
    }
    final r = msg['result'];
    if (r is String) return r;
  }
  return null;
}

int? _asInt(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    if (s.length <= 2) return 0;
    return int.tryParse(s.substring(2), radix: 16);
  }
  return int.tryParse(s);
}

String _normHex(String s) {
  final t = s.trim();
  if (t.isEmpty) return t;
  if (t.startsWith('0x') || t.startsWith('0X')) return '0x${t.substring(2)}';
  return '0x$t';
}
