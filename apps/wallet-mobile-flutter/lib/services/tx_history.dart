// Local transaction history.
//
// The chain cannot reconstruct this for us: the node has no
// address-history RPC, `tx.getTransactionByHash` omits the timestamp and
// returns raw hex account keys, and `tx.getReceipt` carries no status. So
// every transaction this device broadcasts is recorded HERE at the
// `signAndBroadcast` choke point (all eight send paths funnel through it),
// then its status is refreshed against `tx.getStatus` — the one reliable
// status surface — whenever the history UI looks at it.
//
// Records are per-sender-address JSON lists in SharedPreferences, newest
// first, capped so an automated dapp cannot grow the store without bound.
// This is display metadata only — nothing here is consulted when signing.

import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'rpc.dart';

/// Terminal + transient states the UI understands. `pending` is the only
/// non-terminal one; `dropped` means the node no longer knows the hash
/// (silently evicted or never propagated) — funds did not move.
class TxStatus {
  static const pending = 'pending';
  static const confirmed = 'confirmed';
  static const rejected = 'rejected';
  static const dropped = 'dropped';
  static const terminal = {confirmed, rejected, dropped};
}

class TxRecord {
  final String hash;
  final String from; // bech32m anim1… of the signing account
  final String to; // bech32m / contract label — whatever the flow knew
  final String kind; // transfer | deploy | call
  final BigInt amountNanos;
  final BigInt feeNanos; // worst-case offered fee = gasLimit × gasPrice
  final int timestampMs; // wall clock at broadcast
  final String status;
  final int? blockHeight;
  final int? confirmations;
  final String? note; // e.g. the node's rejection reason
  final int misses; // consecutive not_found answers (drop needs a streak)

  const TxRecord({
    required this.hash,
    required this.from,
    required this.to,
    required this.kind,
    required this.amountNanos,
    required this.feeNanos,
    required this.timestampMs,
    this.status = TxStatus.pending,
    this.blockHeight,
    this.confirmations,
    this.note,
    this.misses = 0,
  });

  TxRecord copyWith({
    String? status,
    int? blockHeight,
    int? confirmations,
    String? note,
    int? misses,
  }) =>
      TxRecord(
        hash: hash,
        from: from,
        to: to,
        kind: kind,
        amountNanos: amountNanos,
        feeNanos: feeNanos,
        timestampMs: timestampMs,
        status: status ?? this.status,
        blockHeight: blockHeight ?? this.blockHeight,
        confirmations: confirmations ?? this.confirmations,
        note: note ?? this.note,
        misses: misses ?? this.misses,
      );

  Map<String, dynamic> toJson() => {
        'hash': hash,
        'from': from,
        'to': to,
        'kind': kind,
        'amountNanos': amountNanos.toString(),
        'feeNanos': feeNanos.toString(),
        'timestampMs': timestampMs,
        'status': status,
        if (blockHeight != null) 'blockHeight': blockHeight,
        if (confirmations != null) 'confirmations': confirmations,
        if (note != null) 'note': note,
        if (misses != 0) 'misses': misses,
      };

  static TxRecord fromJson(Map<String, dynamic> j) => TxRecord(
        hash: j['hash'] as String,
        from: j['from'] as String? ?? '',
        to: j['to'] as String? ?? '',
        kind: j['kind'] as String? ?? 'transfer',
        amountNanos: BigInt.tryParse(j['amountNanos'] as String? ?? '0') ??
            BigInt.zero,
        feeNanos:
            BigInt.tryParse(j['feeNanos'] as String? ?? '0') ?? BigInt.zero,
        timestampMs: j['timestampMs'] as int? ?? 0,
        status: j['status'] as String? ?? TxStatus.pending,
        blockHeight: j['blockHeight'] as int?,
        confirmations: j['confirmations'] as int?,
        note: j['note'] as String?,
        misses: j['misses'] as int? ?? 0,
      );

  /// Build a record from a canonical body at broadcast time. `displayTo`
  /// carries the human-readable recipient when the calling flow has one;
  /// the body itself only holds 32-byte digests, which cannot be turned
  /// back into a full anim1… address (the alg id is not in the body).
  static TxRecord fromBody({
    required String hash,
    required String from,
    required Map<String, dynamic> body,
    String? displayTo,
    required int timestampMs,
  }) {
    final payload = body['payload'];
    final t = payload is Map ? payload['t'] : null;
    final kind = t == 1 ? 'deploy' : (t == 2 ? 'call' : 'transfer');
    BigInt amount = BigInt.zero;
    if (payload is Map && payload['v'] is Map) {
      final a = (payload['v'] as Map)['amount'];
      if (a is BigInt) amount = a;
      if (a is int) amount = BigInt.from(a);
    }
    BigInt fee = BigInt.zero;
    final gas = body['gas'];
    if (gas is Map) {
      final price = gas['price'], limit = gas['limit'];
      if (price is int && limit is int) {
        fee = BigInt.from(price) * BigInt.from(limit);
      }
    }
    return TxRecord(
      hash: hash,
      from: from,
      to: displayTo ??
          switch (kind) {
            'deploy' => 'New contract',
            'call' => 'Contract call',
            _ => 'Transfer',
          },
      kind: kind,
      amountNanos: amount,
      feeNanos: fee,
      timestampMs: timestampMs,
    );
  }
}

/// Interpret a `tx.getStatus` result the same way the send screen does.
/// `null`/empty means the RPC was unreachable — keep the current status.
/// Returns null when nothing should change.
TxRecord? applyNodeStatus(TxRecord rec, Map<String, dynamic>? st,
    {required int nowMs}) {
  if (st == null) return null;
  final status = (st['status'] ?? '').toString();
  final state = (st['state'] ?? '').toString();
  final height = st['included_height'] ?? st['blockNumber'];
  final conf = st['confirmations'];
  if (status == 'finalized' || status == 'confirmed' || height != null) {
    return rec.copyWith(
      status: TxStatus.confirmed,
      blockHeight: height is int ? height : rec.blockHeight,
      confirmations: conf is int ? conf : rec.confirmations,
    );
  }
  if (status == 'rejected' || state == 'rejected') {
    final reason = st['reason'] ?? st['rejection_details'];
    return rec.copyWith(
        status: TxStatus.rejected,
        note: reason?.toString() ?? rec.note);
  }
  if (status == 'not_found') {
    // One not_found can be a lagging failover endpoint or a freshly
    // restarted node, not a drop — the multi-endpoint RPC client makes a
    // single stale answer a real input. Declaring "dropped" (the UI says
    // funds did NOT move, inviting a re-send) takes a STREAK of misses
    // AND a ten-minute grace for propagation, mirroring the send
    // tracker's discipline.
    final m = rec.misses + 1;
    if (m >= 3 && nowMs - rec.timestampMs > 10 * 60 * 1000) {
      return rec.copyWith(status: TxStatus.dropped, misses: m);
    }
    return rec.copyWith(misses: m);
  }
  // Any other answer proves the node still knows the hash — reset the
  // streak so unrelated blips never accumulate toward "dropped".
  if (rec.misses != 0) return rec.copyWith(misses: 0);
  return null;
}

class TxHistoryStore {
  static const int _cap = 200;
  static String _key(String address) => 'tx_history_v1_$address';

  static Future<List<TxRecord>> list(String address) async {
    final sp = await SharedPreferences.getInstance();
    final raw = sp.getString(_key(address));
    if (raw == null || raw.isEmpty) return const [];
    try {
      final arr = jsonDecode(raw) as List<dynamic>;
      return [
        for (final e in arr)
          if (e is Map<String, dynamic>) TxRecord.fromJson(e)
      ];
    } catch (_) {
      return const [];
    }
  }

  static Future<void> _write(String address, List<TxRecord> recs) async {
    final sp = await SharedPreferences.getInstance();
    final capped = recs.length > _cap ? recs.sublist(0, _cap) : recs;
    await sp.setString(
        _key(address), jsonEncode([for (final r in capped) r.toJson()]));
  }

  static Future<void> add(TxRecord rec) async {
    final recs = await list(rec.from);
    // Re-broadcasts of the same signed tx return the same hash — keep one.
    if (recs.any((r) => r.hash == rec.hash)) return;
    await _write(rec.from, [rec, ...recs]);
  }

  static Future<TxRecord?> find(String address, String hash) async {
    for (final r in await list(address)) {
      if (r.hash == hash) return r;
    }
    return null;
  }

  static Future<void> patch(TxRecord updated) async {
    final recs = await list(updated.from);
    final i = recs.indexWhere((r) => r.hash == updated.hash);
    if (i < 0) return;
    recs[i] = updated;
    await _write(updated.from, recs);
  }

  /// Refresh every non-terminal record against the node. Returns true when
  /// anything changed (callers then invalidate the provider). Best-effort:
  /// RPC failures leave records as they were.
  static Future<bool> refreshPending(RpcClient rpc, String address) async {
    final recs = await list(address);
    final now = DateTime.now().millisecondsSinceEpoch;
    var changed = false;
    for (final r in recs) {
      // confirmed/rejected are truly final. dropped is NOT skipped: a tx
      // wrongly marked dropped by a stale endpoint must be able to heal
      // itself to confirmed on a later look (applyNodeStatus promotes on
      // any confirmed answer regardless of current status).
      if (r.status == TxStatus.confirmed || r.status == TxStatus.rejected) {
        continue;
      }
      try {
        final st = await rpc.txStatus(r.hash);
        final upd = applyNodeStatus(r, st, nowMs: now);
        if (upd != null) {
          await patch(upd);
          changed = true;
        }
      } catch (_) {
        // Unreachable node: leave the record alone.
      }
    }
    return changed;
  }
}

/// History for one address, newest first. Screens `ref.invalidate` this
/// after adding or refreshing records.
final txHistoryProvider =
    FutureProvider.family<List<TxRecord>, String>((ref, address) async {
  final recs = await TxHistoryStore.list(address);
  recs.sort((a, b) => b.timestampMs.compareTo(a.timestampMs));
  return recs;
});
