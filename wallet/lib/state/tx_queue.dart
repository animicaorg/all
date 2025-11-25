/*
 * Animica Wallet — Tx Queue (pending txs, statuses, resend policy)
 *
 * Features
 *  • Enqueue signed transactions and broadcast them via TxService.
 *  • Track lifecycle: queued → broadcasting → pending → mined | failed | dropped | replaced.
 *  • Auto-monitor receipts with backoff; detect replace-by-fee via nonce advancement.
 *  • Optional resend policy using a caller-provided resigner callback.
 *  • JSON hydrate/dehydrate for persistence between sessions.
 *
 * Notes
 *  • We use RpcClient directly to fetch receipts/tx objects for portability.
 *  • Resend requires a Resigner callback that returns a new *signed* tx hex
 *    (same nonce, bumped fees). If absent, we only monitor.
 */

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:riverpod/riverpod.dart';

import '../services/rpc_client.dart';
import '../services/state_service.dart';
import '../services/tx_service.dart';
import 'providers.dart' show
  rpcClientProvider, stateServiceProvider, txServiceProvider;

/// Public status values for UI.
enum TxStatus {
  queued,
  broadcasting,
  pending,    // in mempool or unknown yet
  mined,      // receipt found & success
  failed,     // receipt found & !success
  dropped,    // not in mempool, nonce unchanged for long time
  replaced,   // sender nonce advanced past tx.nonce (another tx mined)
  rejected,   // RPC immediate rejection on send
}

/// A callback to produce a new *signed* tx hex for resend attempts.
/// The implementation should bump fees/priority deterministically based on [attempt] (1-based).
typedef Resigner = Future<String> Function(int attempt);

/// A single tracked transaction in the queue.
class TrackedTx {
  final String id;           // local id
  final String from;         // normalized (0x… or am…)
  final int nonce;           // sender nonce used
  final String signedHex;    // last signed hex that was sent (0x…)
  final List<String> hashes; // tx hashes observed (hash may change on resend)
  final String? to;          // optional
  final String? valueHex;    // optional 0x… smallest units
  final DateTime createdAt;
  final DateTime updatedAt;
  final TxStatus status;
  final String? error;
  final int resendCount;
  final DateTime? nextResendAt;

  // Not serialized: caller-provided function; you can restore it ad-hoc.
  final Resigner? _resigner;

  const TrackedTx({
    required this.id,
    required this.from,
    required this.nonce,
    required this.signedHex,
    required this.hashes,
    required this.createdAt,
    required this.updatedAt,
    required this.status,
    this.to,
    this.valueHex,
    this.error,
    this.resendCount = 0,
    this.nextResendAt,
    Resigner? resigner,
  }) : _resigner = resigner;

  TrackedTx copyWith({
    String? id,
    String? from,
    int? nonce,
    String? signedHex,
    List<String>? hashes,
    DateTime? createdAt,
    DateTime? updatedAt,
    TxStatus? status,
    String? to,
    String? valueHex,
    String? error,
    int? resendCount,
    DateTime? nextResendAt,
    Resigner? resigner, // will overwrite if provided (even null)
    bool keepResigner = true,
  }) {
    return TrackedTx(
      id: id ?? this.id,
      from: from ?? this.from,
      nonce: nonce ?? this.nonce,
      signedHex: signedHex ?? this.signedHex,
      hashes: hashes ?? this.hashes,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      status: status ?? this.status,
      to: to ?? this.to,
      valueHex: valueHex ?? this.valueHex,
      error: error,
      resendCount: resendCount ?? this.resendCount,
      nextResendAt: nextResendAt ?? this.nextResendAt,
      resigner: keepResigner ? (resigner ?? _resigner) : resigner,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'from': from,
        'nonce': nonce,
        'signedHex': signedHex,
        'hashes': hashes,
        'to': to,
        'valueHex': valueHex,
        'createdAt': createdAt.toIso8601String(),
        'updatedAt': updatedAt.toIso8601String(),
        'status': status.name,
        'error': error,
        'resendCount': resendCount,
        'nextResendAt': nextResendAt?.toIso8601String(),
        // NOTE: resigner is intentionally not serialized.
      };

  factory TrackedTx.fromJson(Map<String, dynamic> m) => TrackedTx(
        id: (m['id'] ?? '').toString(),
        from: (m['from'] ?? '').toString(),
        nonce: int.tryParse('${m['nonce']}') ?? 0,
        signedHex: (m['signedHex'] ?? '').toString(),
        hashes: (m['hashes'] is List)
            ? (m['hashes'] as List).map((e) => e.toString()).toList()
            : <String>[],
        to: (m['to'] ?? (m['toAddress'] ?? '')) == null
            ? null
            : (m['to'] ?? m['toAddress']).toString(),
        valueHex: (m['valueHex'] ?? (m['value'] ?? '')) == null
            ? null
            : (m['valueHex'] ?? m['value']).toString(),
        createdAt: DateTime.tryParse((m['createdAt'] ?? '').toString()) ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        updatedAt: DateTime.tryParse((m['updatedAt'] ?? '').toString()) ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        status: _statusFrom((m['status'] ?? 'queued').toString()),
        error: m['error']?.toString(),
        resendCount: int.tryParse('${m['resendCount'] ?? 0}') ?? 0,
        nextResendAt: (m['nextResendAt'] != null)
            ? DateTime.tryParse(m['nextResendAt'].toString())
            : null,
        // resigner not restored automatically
      );

  String? get lastHash => hashes.isEmpty ? null : hashes.last;
}

TxStatus _statusFrom(String s) {
  return TxStatus.values.firstWhere(
    (e) => e.name == s,
    orElse: () => TxStatus.queued,
  );
}

/// State bag for the queue.
class TxQueueState {
  final Map<String, TrackedTx> byId;
  final List<String> order; // newest first for UI convenience
  const TxQueueState({this.byId = const {}, this.order = const []});

  TxQueueState copyWith({
    Map<String, TrackedTx>? byId,
    List<String>? order,
  }) =>
      TxQueueState(byId: byId ?? this.byId, order: order ?? this.order);

  List<TrackedTx> get all =>
      order.map((id) => byId[id]).whereType<TrackedTx>().toList(growable: false);

  Map<String, dynamic> toJson() => {
        'items': all.map((t) => t.toJson()).toList(),
      };

  factory TxQueueState.fromJson(Map<String, dynamic> m) {
    final items = <TrackedTx>[];
    if (m['items'] is List) {
      for (final e in (m['items'] as List)) {
        if (e is Map) items.add(TrackedTx.fromJson(e.cast<String, dynamic>()));
      }
    }
    final map = {for (final t in items) t.id: t};
    final ord = items.map((t) => t.id).toList();
    ord.sort((a, b) => map[b]!.createdAt.compareTo(map[a]!.createdAt));
    return TxQueueState(byId: map, order: ord);
  }
}

class TxQueueNotifier extends StateNotifier<TxQueueState> {
  final Ref _ref;
  Timer? _monitor;
  final _rand = Random();

  // Resend policy knobs
  static const int _maxResends = 3;
  static const List<Duration> _resendSchedule = [
    Duration(seconds: 45),
    Duration(seconds: 90),
    Duration(seconds: 180),
  ];

  TxQueueNotifier(this._ref) : super(const TxQueueState()) {
    // Background monitor to check receipts & resends.
    _monitor = Timer.periodic(const Duration(seconds: 7), (_) => _tick());
  }

  // ------------ Public API ------------

  /// Add a pre-signed tx for [from] with [nonce]. Optionally provide [to]/[valueHex] for UI.
  /// If you pass [resigner], we may auto-resend on stalls with increased priority.
  TrackedTx enqueueSigned({
    required String from,
    required int nonce,
    required String signedHex,
    String? to,
    String? valueHex,
    Resigner? resigner,
  }) {
    final id = _makeId();
    final now = DateTime.now().toUtc();
    final t = TrackedTx(
      id: id,
      from: _normAddr(from),
      nonce: nonce,
      signedHex: _normHex(signedHex),
      hashes: const [],
      to: to,
      valueHex: valueHex,
      createdAt: now,
      updatedAt: now,
      status: TxStatus.queued,
      resigner: resigner,
    );
    _insert(t);
    // fire-and-forget broadcast
    _broadcast(id);
    return t;
  }

  /// Attach (or replace) a resigner for an existing tracked tx (e.g., after restore).
  void attachResigner(String id, Resigner? resigner) {
    final t = state.byId[id];
    if (t == null) return;
    _update(t.copyWith(resigner: resigner, keepResigner: false));
  }

  /// Remove a tx from queue (local only).
  void remove(String id) {
    final map = Map<String, TrackedTx>.from(state.byId)..remove(id);
    final ord = state.order.where((x) => x != id).toList(growable: false);
    state = state.copyWith(byId: map, order: ord);
  }

  /// Clear finished items (mined/failed/replaced/dropped) older than [olderThan].
  void gc({Duration olderThan = const Duration(hours: 24)}) {
    final cutoff = DateTime.now().toUtc().subtract(olderThan);
    final retained = <String, TrackedTx>{};
    for (final e in state.byId.entries) {
      final t = e.value;
      final done = {
        TxStatus.mined,
        TxStatus.failed,
        TxStatus.replaced,
        TxStatus.dropped,
        TxStatus.rejected,
      }.contains(t.status);
      if (!done || t.updatedAt.isAfter(cutoff)) {
        retained[e.key] = t;
      }
    }
    final ord = state.order.where((id) => retained.containsKey(id)).toList();
    state = state.copyWith(byId: retained, order: ord);
  }

  /// Export/import
  Map<String, dynamic> dehydrate() => state.toJson();
  void hydrate(Map<String, dynamic>? json) {
    if (json == null) return;
    state = TxQueueState.fromJson(json);
  }

  // ------------ Internal ops ------------

  void _insert(TrackedTx t) {
    final map = Map<String, TrackedTx>.from(state.byId);
    final ord = List<String>.from(state.order);
    map[t.id] = t;
    ord.insert(0, t.id);
    state = state.copyWith(byId: map, order: ord);
  }

  void _update(TrackedTx t) {
    final map = Map<String, TrackedTx>.from(state.byId);
    if (!map.containsKey(t.id)) return;
    map[t.id] = t;
    state = state.copyWith(byId: map);
  }

  Future<void> _broadcast(String id) async {
    final txSvc = _ref.read(txServiceProvider);
    final t0 = state.byId[id];
    if (t0 == null) return;

    _update(t0.copyWith(status: TxStatus.broadcasting, updatedAt: DateTime.now().toUtc()));

    try {
      final hash = await txSvc.sendRawTransaction(t0.signedHex);
      final t1 = state.byId[id];
      if (t1 == null) return;
      final hashes = [...t1.hashes, hash];
      _update(t1.copyWith(
        status: TxStatus.pending,
        hashes: hashes,
        updatedAt: DateTime.now().toUtc(),
        error: null,
      ));
    } catch (e) {
      final t1 = state.byId[id];
      if (t1 == null) return;
      _update(t1.copyWith(
        status: TxStatus.rejected,
        error: 'send failed: $e',
        updatedAt: DateTime.now().toUtc(),
      ));
    }
  }

  Future<void> _tick() async {
    // Iterate newest→oldest; keep work bounded.
    final list = state.all.take(30).toList(growable: false);
    for (final t in list) {
      switch (t.status) {
        case TxStatus.pending:
        case TxStatus.broadcasting:
        case TxStatus.queued:
          await _monitorOne(t);
          break;
        default:
          // finished states: noop
          break;
      }
    }
  }

  Future<void> _monitorOne(TrackedTx t) async {
    // If still queued, try broadcast.
    if (t.status == TxStatus.queued) {
      unawaited(_broadcast(t.id));
      return;
    }

    // If we have a hash, try to find a receipt.
    final hash = t.lastHash;
    final now = DateTime.now().toUtc();
    final rpc = _ref.read(rpcClientProvider);
    final stateSvc = _ref.read(stateServiceProvider);

    if (hash != null && hash.isNotEmpty) {
      final receipt = await _getReceipt(rpc, hash);
      if (receipt != null) {
        final success = _asBool(receipt['status'], true);
        _update(t.copyWith(
          status: success ? TxStatus.mined : TxStatus.failed,
          updatedAt: now,
          error: success ? null : 'execution reverted',
        ));
        return;
      }
    }

    // No receipt yet; check if sender nonce advanced past our nonce (replacement).
    try {
      final currentNonce = await stateSvc.getNonce(t.from);
      if (currentNonce > t.nonce) {
        // Our nonce is no longer live → replaced (or mined under different hash)
        _update(t.copyWith(
          status: TxStatus.replaced,
          updatedAt: now,
        ));
        return;
      }
    } catch (_) {
      // ignore
    }

    // Heuristics: if tx isn't visible in mempool AND resend policy allows → attempt resend.
    final shouldResend = await _shouldResend(t, rpc, now);
    if (shouldResend && t._resigner != null && t.resendCount < _maxResends) {
      final attempt = t.resendCount + 1;
      try {
        final newSigned = await t._resigner!(attempt);
        final newHex = _normHex(newSigned);
        final txSvc = _ref.read(txServiceProvider);
        final newHash = await txSvc.sendRawTransaction(newHex);
        _update(t.copyWith(
          signedHex: newHex,
          hashes: [...t.hashes, newHash],
          resendCount: attempt,
          updatedAt: now,
          status: TxStatus.pending,
          error: null,
          nextResendAt: _nextResendAt(attempt, now),
        ));
        return;
      } catch (e) {
        _update(t.copyWith(
          status: TxStatus.pending, // keep pending; next tick may try again
          error: 'resend failed: $e',
          updatedAt: now,
          nextResendAt: _nextResendAt(t.resendCount + 1, now),
        ));
        return;
      }
    }

    // If we can't resend and it's been quite a while with no mempool presence,
    // mark dropped (soft). UI may offer manual "retry".
    if (t.nextResendAt != null &&
        now.isAfter(t.nextResendAt!) &&
        (t._resigner == null || t.resendCount >= _maxResends)) {
      // Also confirm it's not visible
      final visible = await _isTxVisible(rpc, hash);
      if (!visible) {
        _update(t.copyWith(
          status: TxStatus.dropped,
          updatedAt: now,
          error: 'not seen in mempool for a long time',
        ));
      }
    } else {
      // keep as pending; update timestamp occasionally
      if (now.difference(t.updatedAt).inMinutes >= 2) {
        _update(t.copyWith(updatedAt: now));
      }
    }
  }

  Future<bool> _shouldResend(TrackedTx t, RpcClient rpc, DateTime now) async {
    // Need a schedule and a time gate.
    final gate = t.nextResendAt ?? _nextResendAt(t.resendCount, t.createdAt);
    if (now.isBefore(gate)) return false;

    // Only resend if tx is not visible in mempool or has been pending too long.
    final hash = t.lastHash;
    if (hash == null) return true; // no hash recorded (odd), allow resend

    final visible = await _isTxVisible(rpc, hash);
    if (!visible) return true;

    // Visible but stuck: allow resend on later gates.
    return now.difference(gate).inSeconds > 20;
  }

  DateTime _nextResendAt(int attempt, DateTime base) {
    if (attempt <= 0) return base.add(_jitter(_resendSchedule.first));
    final idx = min(attempt - 1, _resendSchedule.length - 1);
    return DateTime.now().toUtc().add(_jitter(_resendSchedule[idx]));
  }

  Duration _jitter(Duration d) {
    final ms = d.inMilliseconds;
    final delta = (_rand.nextDouble() * ms * 0.25).toInt(); // ±25%
    final sign = _rand.nextBool() ? 1 : -1;
    final out = (ms + sign * delta).clamp(10 * 1000, 10 * 60 * 1000);
    return Duration(milliseconds: out);
  }

  // ------------ RPC helpers ------------

  Future<Map<String, dynamic>?> _getReceipt(RpcClient rpc, String hash) async {
    final h = _normHex(hash);
    // Animica name
    try {
      final r = await rpc.call<dynamic>('animica_getTransactionReceipt', [h]);
      if (r is Map) return r.cast<String, dynamic>();
      if (r == null) return null;
    } catch (_) {}
    // ETH-like
    try {
      final r = await rpc.call<dynamic>('eth_getTransactionReceipt', [h]);
      if (r is Map) return r.cast<String, dynamic>();
      if (r == null) return null;
    } catch (_) {}
    return null;
  }

  Future<bool> _isTxVisible(RpcClient rpc, String? hash) async {
    if (hash == null || hash.isEmpty) return false;
    final h = _normHex(hash);
    try {
      final r = await rpc.call<dynamic>('animica_getTransactionByHash', [h]);
      if (r == null) return false;
      final m = (r is Map) ? r.cast<String, dynamic>() : <String, dynamic>{};
      // If blockNumber is null, it's in mempool; if non-null, it's mined (we should've seen receipt).
      return true;
    } catch (_) {}
    try {
      final r = await rpc.call<dynamic>('eth_getTransactionByHash', [h]);
      if (r == null) return false;
      return true;
    } catch (_) {}
    return false;
  }

  // ------------ utils ------------

  String _makeId() => DateTime.now().microsecondsSinceEpoch.toString();

  String _normHex(String s) {
    final t = s.trim();
    if (t.startsWith('0x') || t.startsWith('0X')) return '0x${t.substring(2)}';
    return '0x$t';
  }

  String _normAddr(String s) {
    final t = s.trim();
    if (t.startsWith('0x') || t.startsWith('0X')) return '0x${t.substring(2).toLowerCase()}';
    if (t.startsWith('am')) return t.toLowerCase();
    return t.toLowerCase();
  }

  @override
  void dispose() {
    _monitor?.cancel();
    super.dispose();
  }
}

/// Provider exposing the tx queue.
final txQueueProvider =
    StateNotifierProvider<TxQueueNotifier, TxQueueState>((ref) {
  return TxQueueNotifier(ref);
});

/// Small selectors
final pendingTxsProvider = Provider<List<TrackedTx>>((ref) {
  final q = ref.watch(txQueueProvider);
  return q.all.where((t) => {
        TxStatus.queued,
        TxStatus.broadcasting,
        TxStatus.pending,
      }.contains(t.status)).toList(growable: false);
});

final completedTxsProvider = Provider<List<TrackedTx>>((ref) {
  final q = ref.watch(txQueueProvider);
  return q.all.where((t) => {
        TxStatus.mined,
        TxStatus.failed,
        TxStatus.dropped,
        TxStatus.replaced,
        TxStatus.rejected,
      }.contains(t.status)).toList(growable: false);
});

final txByHashProvider = Provider.family<TrackedTx?, String>((ref, hash) {
  final q = ref.watch(txQueueProvider);
  for (final t in q.all) {
    if (t.hashes.contains(hash)) return t;
  }
  return null;
});

// ---- tiny helpers ----
bool _asBool(dynamic v, bool fallback) {
  if (v is bool) return v;
  if (v == null) return fallback;
  final s = v.toString().toLowerCase();
  if (s == '0x1') return true;
  if (s == '0x0') return false;
  if (s == 'true' || s == '1' || s == 'yes') return true;
  if (s == 'false' || s == '0' || s == 'no') return false;
  return fallback;
}
