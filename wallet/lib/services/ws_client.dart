/*
 * Animica Wallet — WebSocket client (auto-reconnect + JSON-RPC helpers)
 *
 * Dependencies (in pubspec.yaml):
 *   web_socket_channel: ^2.4.0
 *
 * Highlights
 *  • Auto-reconnect with exponential backoff + jitter
 *  • Broadcast streams for connection status and incoming messages
 *  • Safe send helpers (raw/text/json)
 *  • Optional JSON-RPC over WS helpers (call/subscribe/unsubscribe)
 *  • Pluggable method names for subscription notifications
 *
 * NOTE: This client is conservative. It cancels pending RPC calls and
 * closes subscription streams on disconnect. Callers may re-subscribe
 * after 'open' status is emitted again. (Auto re-subscribe could be added
 * later if desired.)
 */

import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

import 'env.dart';

enum WsStatus { connecting, open, retrying, closed, error }

class WsConfig {
  final Uri url;
  final Map<String, dynamic> headers;
  final List<String> protocols;

  /// Maximum number of reconnect attempts; if null, retry forever.
  final int? maxReconnects;
  final Duration baseBackoff;     // first delay
  final Duration maxBackoff;      // cap
  final Duration connectTimeout;  // per connect attempt
  final Duration? pingInterval;   // not supported on web; safe to leave null

  /// JSON-RPC over WS method names (compatible with Animica/Ethereum-style).
  final String rpcSubscribeMethod;
  final String rpcUnsubscribeMethod;
  final String rpcNotificationMethod; // e.g. "animica_subscription"

  const WsConfig({
    required this.url,
    this.headers = const {},
    this.protocols = const [],
    this.maxReconnects,
    this.baseBackoff = const Duration(milliseconds: 400),
    this.maxBackoff = const Duration(seconds: 8),
    this.connectTimeout = const Duration(seconds: 12),
    this.pingInterval,
    this.rpcSubscribeMethod = 'animica_subscribe',
    this.rpcUnsubscribeMethod = 'animica_unsubscribe',
    this.rpcNotificationMethod = 'animica_subscription',
  });

  factory WsConfig.fromEnv() {
    final ws = env.rpcWs;
    if (ws == null) {
      throw StateError('No WS endpoint configured in env (WS_URL).');
    }
    return WsConfig(
      url: ws,
      headers: {'user-agent': env.userAgent}, // ignored by browsers
      protocols: const [],
    );
  }
}

class WsClient {
  final WsConfig config;

  WebSocketChannel? _chan;
  StreamSubscription? _chanSub;

  final _statusCtrl = StreamController<WsStatus>.broadcast();
  final _msgCtrl = StreamController<dynamic>.broadcast();

  bool _active = false;
  int _reconnects = 0;
  final _rand = Random.secure();

  // JSON-RPC state (optional usage)
  int _rpcId = DateTime.now().microsecondsSinceEpoch & 0x7fffffff;
  final Map<int, Completer<dynamic>> _pending = {};
  final Map<String, StreamController<dynamic>> _subs = {};

  WsClient(this.config);

  /// Convenience builder using env.rpcWs.
  factory WsClient.fromEnv() => WsClient(WsConfig.fromEnv());

  Stream<WsStatus> get status => _statusCtrl.stream;
  Stream<dynamic> get messages => _msgCtrl.stream;

  bool get isOpen => _chan != null;

  // -------- Lifecycle --------

  /// Begin (or resume) connection and auto-reconnect loop.
  void start() {
    if (_active) return;
    _active = true;
    _connect();
  }

  /// Permanently stop and close the socket; cancels auto-reconnect.
  Future<void> stop() async {
    _active = false;
    await _closeChannel(code: ws_status.normalClosure, reason: 'stop()');
    _statusCtrl.add(WsStatus.closed);
  }

  Future<void> _connect() async {
    if (!_active) return;
    _statusCtrl.add(WsStatus.connecting);

    try {
      // Connect (with timeout)
      final chan = await _connectOnce().timeout(config.connectTimeout);
      _attach(chan);
      _reconnects = 0;
      _statusCtrl.add(WsStatus.open);
    } on TimeoutException {
      await _scheduleReconnect(label: 'timeout');
    } catch (_) {
      await _scheduleReconnect(label: 'connect-error');
    }
  }

  Future<WebSocketChannel> _connectOnce() async {
    // web_socket_channel handles platform specifics under the hood.
    final chan = WebSocketChannel.connect(
      config.url,
      protocols: config.protocols.isEmpty ? null : config.protocols,
    );
    // pingInterval is not portable across platforms via the abstract type.
    // (On IO you could cast to IOWebSocketChannel and set pingInterval.)
    return chan;
  }

  void _attach(WebSocketChannel chan) {
    _chan = chan;
    _chanSub = chan.stream.listen(
      (event) {
        final decoded = _decode(event);
        _routeMessage(decoded);
      },
      onError: (e, st) async {
        _statusCtrl.add(WsStatus.error);
        await _handleDisconnect('stream-error');
      },
      onDone: () async {
        await _handleDisconnect('done');
      },
      cancelOnError: false,
    );
  }

  Future<void> _handleDisconnect(String reason) async {
    if (!_active) {
      // If user explicitly stopped, just mark closed.
      _statusCtrl.add(WsStatus.closed);
      return;
    }
    await _closeChannel(code: ws_status.goingAway, reason: reason);
    await _failPending('disconnected');
    await _closeAllSubscriptions('disconnected');
    await _scheduleReconnect(label: reason);
  }

  Future<void> _scheduleReconnect({required String label}) async {
    if (!_active) return;

    final maxR = config.maxReconnects;
    if (maxR != null && _reconnects >= maxR) {
      _statusCtrl.add(WsStatus.closed);
      return;
    }
    _reconnects += 1;

    // Exponential backoff with jitter
    final factor = pow(2.0, (_reconnects - 1)).toDouble();
    final baseMs = (config.baseBackoff.inMilliseconds * factor).toInt();
    final jitter = _rand.nextInt(200);
    final delay = Duration(
      milliseconds: min(baseMs + jitter, config.maxBackoff.inMilliseconds),
    );
    _statusCtrl.add(WsStatus.retrying);
    await Future.delayed(delay);
    if (_active) _connect();
  }

  Future<void> _closeChannel({int? code, String? reason}) async {
    try {
      await _chanSub?.cancel();
    } catch (_) {}
    _chanSub = null;

    try {
      await _chan?.sink.close(code ?? ws_status.goingAway, reason);
    } catch (_) {}
    _chan = null;
  }

  // -------- Sending --------

  /// Send a raw string frame.
  void sendText(String text) {
    final c = _chan;
    if (c == null) throw StateError('WS not connected');
    c.sink.add(text);
  }

  /// Send bytes.
  void sendBytes(Uint8List bytes) {
    final c = _chan;
    if (c == null) throw StateError('WS not connected');
    c.sink.add(bytes);
  }

  /// Send JSON (Map/List/primitive). Encodes with jsonEncode.
  void sendJson(Object? data) {
    sendText(jsonEncode(data));
  }

  // -------- Receiving --------

  dynamic _decode(dynamic event) {
    if (event is String) {
      try {
        return json.decode(event);
      } catch (_) {
        return event; // plain text
      }
    }
    if (event is List<int>) {
      // If the server sent binary JSON (rare), try utf8→json
      final asBytes = Uint8List.fromList(event);
      try {
        final s = utf8.decode(asBytes);
        return json.decode(s);
      } catch (_) {
        return asBytes;
      }
    }
    return event;
  }

  void _routeMessage(dynamic msg) {
    // JSON-RPC response?
    if (msg is Map && msg['jsonrpc'] == '2.0') {
      // Notification?
      if (msg['method'] != null) {
        _handleRpcNotification(msg);
        return;
      }
      // Response
      final id = msg['id'];
      final intId = (id is int) ? id : int.tryParse('$id');
      final c = intId != null ? _pending.remove(intId) : null;
      if (c != null) {
        if (msg['error'] != null) {
          c.completeError(msg['error']);
        } else {
          c.complete(msg['result']);
        }
        return;
      }
    }

    // Otherwise fan out as a general message.
    _msgCtrl.add(msg);
  }

  void _handleRpcNotification(Map msg) {
    final method = msg['method']?.toString() ?? '';
    if (method != config.rpcNotificationMethod) {
      // Unknown notification; forward to general stream.
      _msgCtrl.add(msg);
      return;
    }
    final params = msg['params'];
    final subId = (params is Map) ? params['subscription']?.toString() : null;
    if (subId == null) {
      _msgCtrl.add(msg);
      return;
    }
    final sc = _subs[subId];
    if (sc == null) {
      // No local subscriber; forward anyway.
      _msgCtrl.add(msg);
      return;
    }
    sc.add(params['result']);
  }

  // -------- JSON-RPC helpers over WS --------

  Future<T> rpcCall<T>(String method, [dynamic params]) {
    final id = ++_rpcId;
    final pay = {
      'jsonrpc': '2.0',
      'id': id,
      'method': method,
      'params': (params == null)
          ? const []
          : (params is List || params is Map ? params : [params]),
    };
    final c = Completer<dynamic>();
    _pending[id] = c;
    sendJson(pay);
    return c.future.timeout(
      const Duration(seconds: 30),
      onTimeout: () {
        _pending.remove(id);
        throw TimeoutException('WS RPC timeout for $method');
      },
    ) as Future<T>;
  }

  /// Subscribe to a server topic. Returns a Stream of results.
  /// Typical usage:
  ///   final heads = await ws.subscribe('newHeads');
  ///   heads.listen((h) => print(h));
  Future<Stream<dynamic>> subscribe(String topic, [dynamic params]) async {
    final subId = await rpcCall<String>(config.rpcSubscribeMethod, [
      topic,
      if (params != null) params,
    ]);
    final sc = StreamController<dynamic>.broadcast(
      onCancel: () async {
        // Auto-cleanup when last listener is gone.
        await unsubscribe(subId);
      },
    );
    _subs[subId] = sc;
    return sc.stream;
  }

  Future<bool> unsubscribe(String subId) async {
    final sc = _subs.remove(subId);
    try {
      await rpcCall<bool>(config.rpcUnsubscribeMethod, [subId]);
    } catch (_) {
      // ignore RPC error on unsubscribe; we still close local stream
    }
    await sc?.close();
    return true;
  }

  // -------- Cleanup helpers --------

  Future<void> _failPending(String why) async {
    if (_pending.isEmpty) return;
    final err = StateError('WS $why; pending calls cancelled');
    final list = _pending.values.toList();
    _pending.clear();
    for (final c in list) {
      if (!c.isCompleted) c.completeError(err);
    }
  }

  Future<void> _closeAllSubscriptions(String why) async {
    if (_subs.isEmpty) return;
    final list = _subs.values.toList();
    _subs.clear();
    for (final sc in list) {
      // Notify listeners that the stream ended due to disconnect.
      try {
        sc.addError(StateError('WS $why; subscription closed'));
      } catch (_) {}
      await sc.close();
    }
  }

  // -------- Dispose --------

  /// Convenience alias to mirror other clients.
  Future<void> close() => dispose();

  Future<void> dispose() async {
    await stop();
    await _statusCtrl.close();
    await _msgCtrl.close();
  }
}
