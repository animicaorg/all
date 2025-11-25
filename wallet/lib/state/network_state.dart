/*
 * Animica Wallet — Network State (RPC URL, Chain ID, Head Height)
 *
 * Riverpod state that tracks the active RPC/WS endpoints, chainId, and the
 * current head height by polling the node. It is resilient to different RPC
 * method names (Animica vs. ETH-like fallbacks).
 *
 * How it works
 *  - On init, we read rpcUrl/wsUrl/chainId from the simple providers in
 *    providers.dart (which you can override from a settings screen or tests).
 *  - We poll every 2s for head height (with jitter). On failures we backoff
 *    up to 10s.
 *  - We also try to detect/refresh the chainId from the node.
 *
 * Intended use
 *   final net = ref.watch(networkStateProvider);
 *   Text('Chain #${net.chainId}  •  head #${net.headHeight}  •  ${net.online ? "online" : "offline"}');
 */

import 'dart:async';
import 'dart:math';
import 'package:riverpod/riverpod.dart';

import '../services/rpc_client.dart';
import 'providers.dart' show
  rpcClientProvider, rpcUrlProvider, wsUrlProvider, chainIdProvider;

/// Immutable snapshot of network info.
class NetworkState {
  final String rpcUrl;
  final String wsUrl;
  final int chainId;
  final int headHeight;       // best known block/height
  final DateTime lastUpdated; // when head was last refreshed
  final bool online;          // last poll succeeded

  const NetworkState({
    required this.rpcUrl,
    required this.wsUrl,
    required this.chainId,
    required this.headHeight,
    required this.lastUpdated,
    required this.online,
  });

  NetworkState copyWith({
    String? rpcUrl,
    String? wsUrl,
    int? chainId,
    int? headHeight,
    DateTime? lastUpdated,
    bool? online,
  }) {
    return NetworkState(
      rpcUrl: rpcUrl ?? this.rpcUrl,
      wsUrl: wsUrl ?? this.wsUrl,
      chainId: chainId ?? this.chainId,
      headHeight: headHeight ?? this.headHeight,
      lastUpdated: lastUpdated ?? this.lastUpdated,
      online: online ?? this.online,
    );
  }

  @override
  String toString() =>
      'NetworkState(chainId:$chainId rpc:$rpcUrl ws:$wsUrl head:$headHeight online:$online @${lastUpdated.toIso8601String()})';

  @override
  int get hashCode => Object.hash(rpcUrl, wsUrl, chainId, headHeight, online, lastUpdated);

  @override
  bool operator ==(Object other) {
    return other is NetworkState &&
        other.rpcUrl == rpcUrl &&
        other.wsUrl == wsUrl &&
        other.chainId == chainId &&
        other.headHeight == headHeight &&
        other.online == online &&
        other.lastUpdated == lastUpdated;
  }
}

class NetworkStateNotifier extends StateNotifier<NetworkState> {
  final Ref _ref;
  RpcClient _rpc;
  Timer? _timer;
  Duration _interval = const Duration(seconds: 2);
  int _consecutiveFailures = 0;
  final _rand = Random();

  NetworkStateNotifier(this._ref, this._rpc)
      : super(NetworkState(
          rpcUrl: _ref.read(rpcUrlProvider),
          wsUrl: _ref.read(wsUrlProvider),
          chainId: _ref.read(chainIdProvider),
          headHeight: 0,
          lastUpdated: DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
          online: false,
        )) {
    // Watch for RPC/WS/chainId changes from settings and apply.
    _ref.listen<String>(rpcUrlProvider, (prev, next) {
      state = state.copyWith(rpcUrl: next);
      // Replace the RpcClient via provider overrides in tests if needed.
      _restartPolling();
    });
    _ref.listen<String>(wsUrlProvider, (prev, next) {
      state = state.copyWith(wsUrl: next);
    });
    _ref.listen<int>(chainIdProvider, (prev, next) async {
      state = state.copyWith(chainId: next);
    });

    // Also react if the rpcClientProvider itself is overridden.
    _ref.listen<RpcClient>(rpcClientProvider, (prev, next) {
      _rpc = next;
      _restartPolling();
    });

    _startPolling();
    _refreshChainId(); // best-effort initial chain id query
  }

  void _startPolling() {
    _timer?.cancel();
    _timer = Timer(_jittered(_interval), _tick);
  }

  void _restartPolling() {
    _consecutiveFailures = 0;
    _interval = const Duration(seconds: 2);
    _startPolling();
  }

  Future<void> _tick() async {
    try {
      final height = await _fetchHead();
      final now = DateTime.now().toUtc();

      // Monotonic (do not regress head unless it is zero).
      final newHead = height >= state.headHeight ? height : state.headHeight;

      state = state.copyWith(
        headHeight: newHead,
        lastUpdated: now,
        online: true,
      );

      // Occasionally also re-check chainId (every ~30 polls).
      if (_rand.nextInt(30) == 0) {
        await _refreshChainId();
      }

      _consecutiveFailures = 0;
      _interval = const Duration(seconds: 2);
    } catch (_) {
      _consecutiveFailures += 1;
      state = state.copyWith(online: false);
      // Exponential backoff up to 10s.
      final s = min(10, 2 << (_consecutiveFailures.clamp(0, 3)));
      _interval = Duration(seconds: s);
    } finally {
      _timer = Timer(_jittered(_interval), _tick);
    }
  }

  Duration _jittered(Duration d) {
    final ms = d.inMilliseconds;
    final jitter = (_rand.nextDouble() * ms * 0.2).toInt(); // ±20%
    final sign = _rand.nextBool() ? 1 : -1;
    final out = (ms + sign * jitter).clamp(500, 15000);
    return Duration(milliseconds: out);
  }

  Future<void> _refreshChainId() async {
    try {
      final id = await _fetchChainId();
      if (id != 0 && id != state.chainId) {
        state = state.copyWith(chainId: id);
      }
    } catch (_) {
      // ignore; not fatal
    }
  }

  // ---- Public helpers (useful in tests) ----

  void setManualHead(int height) {
    state = state.copyWith(
      headHeight: height,
      lastUpdated: DateTime.now().toUtc(),
      online: true,
    );
  }

  // ---- RPC helpers ----

  Future<int> _fetchHead() async {
    // Try Animica method name first.
    try {
      final v = await _rpc.call<dynamic>('animica_blockNumber');
      return _toInt(v);
    } catch (_) {}
    // ETH-like fallback
    try {
      final v = await _rpc.call<dynamic>('eth_blockNumber');
      return _toInt(v);
    } catch (_) {}
    // Fallback: fetch latest header and read its number
    try {
      final hdr = await _rpc.call<dynamic>('animica_getHeaderByNumber', ['latest']);
      final m = _asMap(hdr);
      return _toInt(m['number'] ?? m['height']);
    } catch (_) {}
    try {
      final blk = await _rpc.call<dynamic>('eth_getBlockByNumber', ['latest', false]);
      final m = _asMap(blk);
      return _toInt(m['number']);
    } catch (e) {
      rethrow;
    }
  }

  Future<int> _fetchChainId() async {
    try {
      final v = await _rpc.call<dynamic>('animica_chainId');
      return _toInt(v);
    } catch (_) {}
    try {
      final v = await _rpc.call<dynamic>('eth_chainId');
      return _toInt(v);
    } catch (_) {}
    try {
      final v = await _rpc.call<dynamic>('net_version');
      // net_version returns a decimal string commonly
      if (v is int) return v;
      if (v is String) return int.tryParse(v) ?? 0;
    } catch (_) {}
    return 0;
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}

/// Provider exposing the current [NetworkState].
final networkStateProvider =
    StateNotifierProvider<NetworkStateNotifier, NetworkState>((ref) {
  final rpc = ref.watch(rpcClientProvider);
  return NetworkStateNotifier(ref, rpc);
});

// ===== small utils =====

Map<String, dynamic> _asMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, v) => MapEntry(k.toString(), v));
  throw FormatException('Expected map, got ${v.runtimeType}');
}

int _toInt(dynamic v) {
  if (v == null) return 0;
  if (v is int) return v;
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    return s.length <= 2 ? 0 : int.parse(s.substring(2), radix: 16);
  }
  return int.tryParse(s) ?? 0;
}
