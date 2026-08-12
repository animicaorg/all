// Local tx history: record building, JSON round-trip, node-status mapping,
// and the SharedPreferences-backed store.

import 'package:animica_wallet/services/tx_history.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

TxRecord _rec({String hash = '0xabc', String status = TxStatus.pending}) =>
    TxRecord(
      hash: hash,
      from: 'anim1sender',
      to: 'anim1recipient',
      kind: 'transfer',
      amountNanos: BigInt.from(5) * BigInt.from(1000000000),
      feeNanos: BigInt.from(21000),
      timestampMs: 1000,
      status: status,
    );

void main() {
  test('JSON round-trip preserves every field', () {
    final r = _rec().copyWith(
        status: TxStatus.confirmed, blockHeight: 70900, confirmations: 3);
    final back = TxRecord.fromJson(r.toJson());
    expect(back.hash, r.hash);
    expect(back.from, r.from);
    expect(back.to, r.to);
    expect(back.amountNanos, r.amountNanos);
    expect(back.feeNanos, r.feeNanos);
    expect(back.timestampMs, r.timestampMs);
    expect(back.status, TxStatus.confirmed);
    expect(back.blockHeight, 70900);
    expect(back.confirmations, 3);
  });

  test('fromBody extracts kind, amount, and worst-case fee', () {
    final body = {
      'gas': {'price': 1, 'limit': 21000},
      'payload': {
        't': 0,
        'v': {'amount': BigInt.from(123)},
      },
    };
    final r = TxRecord.fromBody(
        hash: '0x1',
        from: 'anim1x',
        body: body,
        displayTo: 'anim1y',
        timestampMs: 42);
    expect(r.kind, 'transfer');
    expect(r.amountNanos, BigInt.from(123));
    expect(r.feeNanos, BigInt.from(21000));
    expect(r.to, 'anim1y');
  });

  group('applyNodeStatus', () {
    test('finalized -> confirmed with height + confirmations', () {
      final upd = applyNodeStatus(
          _rec(),
          {'status': 'finalized', 'included_height': 70901, 'confirmations': 5},
          nowMs: 2000);
      expect(upd?.status, TxStatus.confirmed);
      expect(upd?.blockHeight, 70901);
      expect(upd?.confirmations, 5);
    });

    test('rejected carries the reason', () {
      final upd = applyNodeStatus(
          _rec(), {'status': 'rejected', 'reason': 'insufficient_funds'},
          nowMs: 2000);
      expect(upd?.status, TxStatus.rejected);
      expect(upd?.note, 'insufficient_funds');
    });

    test('a drop takes a 3-miss streak AND the 10-minute grace', () {
      final old = 1000 + 11 * 60 * 1000;
      // A single not_found — even on an old record — only counts a miss:
      // one stale failover endpoint must never produce "funds did not
      // leave your account" for money that moved.
      var r = _rec();
      var upd = applyNodeStatus(r, {'status': 'not_found'}, nowMs: old);
      expect(upd?.status, TxStatus.pending);
      expect(upd?.misses, 1);
      upd = applyNodeStatus(upd!, {'status': 'not_found'}, nowMs: old);
      expect(upd?.status, TxStatus.pending);
      expect(upd?.misses, 2);
      upd = applyNodeStatus(upd!, {'status': 'not_found'}, nowMs: old);
      expect(upd?.status, TxStatus.dropped);
    });

    test('three fresh not_founds still wait out the grace period', () {
      var r = _rec().copyWith(misses: 2);
      final upd =
          applyNodeStatus(r, {'status': 'not_found'}, nowMs: 5000);
      expect(upd?.status, TxStatus.pending);
      expect(upd?.misses, 3);
    });

    test('any known answer resets the miss streak', () {
      final r = _rec().copyWith(misses: 2);
      final upd = applyNodeStatus(r, {'status': 'seen'}, nowMs: 2000);
      expect(upd?.misses, 0);
      expect(upd?.status, TxStatus.pending);
    });

    test('a wrongly-dropped record heals to confirmed', () {
      final r = _rec(status: TxStatus.dropped).copyWith(misses: 3);
      final upd = applyNodeStatus(
          r,
          {'status': 'finalized', 'included_height': 70950},
          nowMs: 2000);
      expect(upd?.status, TxStatus.confirmed);
      expect(upd?.blockHeight, 70950);
    });

    test('null (unreachable node) changes nothing', () {
      expect(applyNodeStatus(_rec(), null, nowMs: 2000), null);
    });
  });

  group('TxHistoryStore', () {
    setUp(() => SharedPreferences.setMockInitialValues({}));

    test('add + list newest first, duplicate hashes kept once', () async {
      await TxHistoryStore.add(_rec(hash: '0x1'));
      await TxHistoryStore.add(_rec(hash: '0x2'));
      await TxHistoryStore.add(_rec(hash: '0x2')); // re-broadcast, same hash
      final recs = await TxHistoryStore.list('anim1sender');
      expect(recs.length, 2);
      expect(recs.first.hash, '0x2');
    });

    test('patch updates in place, find retrieves by hash', () async {
      await TxHistoryStore.add(_rec(hash: '0x1'));
      final r = (await TxHistoryStore.find('anim1sender', '0x1'))!;
      await TxHistoryStore.patch(
          r.copyWith(status: TxStatus.confirmed, blockHeight: 71000));
      final again = await TxHistoryStore.find('anim1sender', '0x1');
      expect(again?.status, TxStatus.confirmed);
      expect(again?.blockHeight, 71000);
      expect((await TxHistoryStore.list('anim1sender')).length, 1);
    });

    test('history is per-address', () async {
      await TxHistoryStore.add(_rec(hash: '0x1'));
      expect(await TxHistoryStore.list('anim1other'), isEmpty);
    });
  });
}
