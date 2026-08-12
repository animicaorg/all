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
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../constants.dart';
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

class TxDirection {
  static const sent = 'sent';
  static const received = 'received';
}

class TxRecord {
  final String hash;
  final String account; // bech32m of the wallet account this record belongs to
  final String direction; // TxDirection.sent | TxDirection.received
  final String from; // bech32m of the tx sender
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
    String? account,
    this.direction = TxDirection.sent,
    this.status = TxStatus.pending,
    this.blockHeight,
    this.confirmations,
    this.note,
    this.misses = 0,
  }) : account = account ?? from;

  TxRecord copyWith({
    String? status,
    int? blockHeight,
    int? confirmations,
    String? note,
    int? misses,
  }) =>
      TxRecord(
        hash: hash,
        account: account,
        direction: direction,
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
        'account': account,
        'direction': direction,
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
        // Pre-0.5.0 records carried neither field: they were all written by
        // this device's own broadcasts, so account==from and direction=sent.
        account: j['account'] as String? ?? j['from'] as String?,
        direction: j['direction'] as String? ?? TxDirection.sent,
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
    // A record with recorded inclusion must NEVER be demoted by a node
    // that merely can't find the hash (lagging failover endpoint, pruned
    // index): "dropped — funds did not move" on money that moved invites
    // a duplicate payment. Only an explicit rejected answer moves it.
    if (rec.status == TxStatus.confirmed || rec.blockHeight != null) {
      return null;
    }
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

  /// Serializes every read-modify-write of the record lists. Concurrent
  /// mutators are real here — the send tracker, the history screen's
  /// refresh, and the explorer ingest all await between read and write —
  /// and an interleaving would silently drop whichever record lost.
  static Future<void> _mutex = Future.value();
  static Future<T> _serialized<T>(Future<T> Function() op) {
    final result = _mutex.then((_) => op());
    _mutex = result.then((_) {}, onError: (_) {});
    return result;
  }

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

  /// Returns true when anything changed. Re-broadcasts of the same signed
  /// tx return the same hash and are kept once — but a chain-confirmed
  /// duplicate HEALS a local record stuck pending/dropped (the explorer saw
  /// it mined; the local status is stale), without touching richer local
  /// fields like the display recipient or broadcast timestamp.
  static Future<bool> add(TxRecord rec) => _serialized(() async {
        final recs = await list(rec.account);
        final i = recs.indexWhere((r) => r.hash == rec.hash);
        if (i >= 0) {
          final existing = recs[i];
          if (rec.status == TxStatus.confirmed &&
              existing.status != TxStatus.confirmed) {
            recs[i] = existing.copyWith(
              status: TxStatus.confirmed,
              blockHeight: rec.blockHeight ?? existing.blockHeight,
              misses: 0,
            );
            await _write(rec.account, recs);
            return true;
          }
          return false;
        }
        await _write(rec.account, [rec, ...recs]);
        return true;
      });

  static Future<TxRecord?> find(String address, String hash) async {
    for (final r in await list(address)) {
      if (r.hash == hash) return r;
    }
    return null;
  }

  static Future<void> patch(TxRecord updated) => _serialized(() async {
        final recs = await list(updated.account);
        final i = recs.indexWhere((r) => r.hash == updated.hash);
        if (i < 0) return;
        recs[i] = updated;
        await _write(updated.account, recs);
      });

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

  static const int _ingestMaxPages = 6;
  static String _scanKey(String address) => 'tx_history_scan_v1_$address';
  static String _pendingTopKey(String address) =>
      'tx_history_scan_pending_top_v1_$address';
  static String _pendingFloorKey(String address) =>
      'tx_history_scan_pending_floor_v1_$address';

  /// Pull on-chain transactions touching [address] from the explorer's
  /// address index — incoming transfers AND sends made from other devices —
  /// so the history is the account's, not just this phone's.
  ///
  /// The explorer scans blocks newest-first with cursor pagination
  /// (`nextCursor` = the first height it has NOT scanned). A per-address
  /// high-water mark keeps refreshes incremental — but it only advances
  /// once a scan PROVABLY connects to prior coverage (reached the old
  /// watermark or genesis). A scan interrupted by the page cap or an
  /// explorer error instead persists a resume cursor, and the next refresh
  /// continues paging DOWN from there until the gap closes — so no block
  /// range is ever silently skipped, no matter how long the wallet was
  /// closed. Returns true when anything new was stored.
  static Future<bool> ingestIncoming(
    String address, {
    String? apiBase,
    http.Client? client,
  }) async {
    final base = apiBase ?? AnimicaConfig.explorerApiUrl;
    if (base.isEmpty) return false;
    final sp = await SharedPreferences.getInstance();
    final watermark = sp.getInt(_scanKey(address)) ?? 0;
    // Resume state from an interrupted scan: the head that scan started
    // from, and the floor it had reached when it stopped.
    int? scanTop = sp.getInt(_pendingTopKey(address));
    int? lastFloor = sp.getInt(_pendingFloorKey(address));
    String? cursor = lastFloor?.toString();
    final httpc = client ?? http.Client();
    var changed = false;
    var coveredGap = false;
    try {
      for (var page = 0; page < _ingestMaxPages; page++) {
        final uri = Uri.parse(
            '$base/address/$address?limit=50${cursor != null ? '&cursor=$cursor' : ''}');
        final resp = await httpc.get(uri).timeout(const Duration(seconds: 25));
        if (resp.statusCode != 200) break;
        final j = jsonDecode(resp.body) as Map<String, dynamic>;
        final txs = (j['txs'] as List?) ?? const [];
        final nextCursor = j['nextCursor']?.toString();
        final scanned = (j['scannedBlocks'] as num?)?.toInt() ?? 0;
        final floor = nextCursor != null ? int.tryParse(nextCursor) : null;
        // First page of a fresh scan establishes the candidate watermark:
        // floor + scanned == the height the scan started at (the head), or
        // scanned - 1 when the whole chain fit in one page.
        scanTop ??= floor != null ? floor + scanned : scanned - 1;

        for (final t in txs) {
          if (t is Map) {
            final added = await _ingestOne(address, t, watermark);
            changed = changed || added;
          }
        }

        if (nextCursor == null || floor == null) {
          coveredGap = true; // reached genesis — full coverage
          lastFloor = null;
          break;
        }
        lastFloor = floor;
        if (floor <= watermark) {
          coveredGap = true; // connected to previously scanned history
          break;
        }
        cursor = nextCursor;
      }
      if (coveredGap) {
        if (scanTop != null && scanTop > watermark) {
          await sp.setInt(_scanKey(address), scanTop);
        }
        await sp.remove(_pendingTopKey(address));
        await sp.remove(_pendingFloorKey(address));
      } else if (scanTop != null && lastFloor != null) {
        // Interrupted (page cap or explorer error mid-scan): do NOT touch
        // the watermark — remember where we got to so the next refresh
        // resumes downward instead of restarting at head and stranding
        // every block in between forever.
        await sp.setInt(_pendingTopKey(address), scanTop);
        await sp.setInt(_pendingFloorKey(address), lastFloor);
      }
    } catch (_) {
      // Explorer unreachable before anything was learned: change nothing.
    } finally {
      if (client == null) httpc.close();
    }
    return changed;
  }

  static Future<bool> _ingestOne(
      String address, Map<dynamic, dynamic> t, int watermark) async {
    final hash = t['hash']?.toString();
    final from = t['from']?.toString() ?? '';
    final to = t['to']?.toString() ?? '';
    if (hash == null || hash.isEmpty) return false;
    if (from != address && to != address) return false;
    final height = (t['blockNumber'] as num?)?.toInt();
    if (height != null && height <= watermark) return false;
    final ts = (t['timestamp'] as num?)?.toInt();
    final gasPrice = _bigIntFromAny(t['gasPrice']);
    final gasLimit = _bigIntFromAny(t['gasLimit']);
    final kindRaw = t['classification'] is Map
        ? (t['classification'] as Map)['type']?.toString()
        : null;
    return TxHistoryStore.add(TxRecord(
      hash: hash,
      account: address,
      direction:
          from == address ? TxDirection.sent : TxDirection.received,
      from: from,
      to: to,
      kind: kindRaw == 'native_transfer' || kindRaw == null
          ? 'transfer'
          : 'call',
      amountNanos: _bigIntFromAny(t['value']) ?? BigInt.zero,
      feeNanos: (gasPrice ?? BigInt.zero) * (gasLimit ?? BigInt.zero),
      // Block time when the explorer has it; epoch-0 records would sort to
      // the bottom, which is the honest place for a tx of unknown age.
      timestampMs: ts != null && ts > 0 ? ts * 1000 : 0,
      status: TxStatus.confirmed,
      blockHeight: height,
    ));
  }
}

/// Decimal or 0x-hex, number or string — the explorer/node emit all four.
BigInt? _bigIntFromAny(dynamic v) {
  if (v == null) return null;
  if (v is int) return BigInt.from(v);
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    return BigInt.tryParse(s.substring(2), radix: 16);
  }
  return BigInt.tryParse(s);
}

/// History for one address, newest first. Screens `ref.invalidate` this
/// after adding or refreshing records.
final txHistoryProvider =
    FutureProvider.family<List<TxRecord>, String>((ref, address) async {
  final recs = await TxHistoryStore.list(address);
  recs.sort((a, b) => b.timestampMs.compareTo(a.timestampMs));
  return recs;
});
