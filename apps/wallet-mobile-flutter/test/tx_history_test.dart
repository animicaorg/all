// Local tx history: record building, JSON round-trip, node-status mapping,
// and the SharedPreferences-backed store.

import 'dart:convert';

import 'package:animica_wallet/services/tx_history.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
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

    test('a CONFIRMED record is never demoted by not_found answers', () {
      var r = _rec(status: TxStatus.confirmed)
          .copyWith(blockHeight: 70900, misses: 0);
      for (var i = 0; i < 5; i++) {
        final upd = applyNodeStatus(r, {'status': 'not_found'},
            nowMs: 1000 + 60 * 60 * 1000);
        expect(upd, null,
            reason: 'a lagging node must not un-confirm mined money');
      }
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

  test('pre-0.5.0 records migrate: account=from, direction=sent', () {
    final r = TxRecord.fromJson({
      'hash': '0xold',
      'from': 'anim1sender',
      'to': 'anim1recipient',
      'kind': 'transfer',
      'amountNanos': '1',
      'feeNanos': '21000',
      'timestampMs': 1,
      'status': 'confirmed',
    });
    expect(r.account, 'anim1sender');
    expect(r.direction, TxDirection.sent);
  });

  group('ingestIncoming (explorer address index)', () {
    const me = 'anim1meaddress';

    Map<String, dynamic> page1() => {
          'address': me,
          'txs': [
            {
              'hash': '0xin',
              'from': 'anim1payer',
              'to': me,
              'value': '5000000000',
              'status': 'confirmed',
              'blockNumber': 71000,
              'timestamp': 1786500000,
              'gasPrice': 1,
              'gasLimit': 21000,
              'classification': {'type': 'native_transfer'},
            },
            {
              'hash': '0xout-other-device',
              'from': me,
              'to': 'anim1shop',
              'value': '1000000000',
              'status': 'confirmed',
              'blockNumber': 70990,
              'timestamp': 1786490000,
              'gasPrice': 1,
              'gasLimit': 21000,
            },
          ],
          'nextCursor': '70900',
          'scannedBlocks': 250,
        };

    setUp(() => SharedPreferences.setMockInitialValues({}));

    test('stores incoming + other-device sends, advances the watermark',
        () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(req.url.path, contains('/address/$me'));
        if (req.url.queryParameters['cursor'] == null) {
          return http.Response(jsonEncode(page1()), 200);
        }
        return http.Response(
            jsonEncode({
              'address': me,
              'txs': [],
              'nextCursor': null,
              'scannedBlocks': 100
            }),
            200);
      });

      final changed = await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      expect(changed, true);
      expect(calls, 2);

      final recs = await TxHistoryStore.list(me);
      expect(recs.length, 2);
      final incoming = recs.firstWhere((r) => r.hash == '0xin');
      expect(incoming.direction, TxDirection.received);
      expect(incoming.status, TxStatus.confirmed);
      expect(incoming.blockHeight, 71000);
      expect(incoming.timestampMs, 1786500000 * 1000);
      expect(incoming.feeNanos, BigInt.from(21000));
      expect(incoming.from, 'anim1payer');
      final other = recs.firstWhere((r) => r.hash == '0xout-other-device');
      expect(other.direction, TxDirection.sent);

      // Second refresh: watermark (70900+250) skips everything — no dupes,
      // no change.
      final again = await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      expect(again, false);
      expect((await TxHistoryStore.list(me)).length, 2);
    });

    test('a tx already recorded locally at broadcast is not duplicated',
        () async {
      await TxHistoryStore.add(TxRecord(
        hash: '0xin',
        account: me,
        from: me,
        to: 'anim1payer',
        kind: 'transfer',
        amountNanos: BigInt.one,
        feeNanos: BigInt.from(21000),
        timestampMs: 5,
      ));
      final client = MockClient(
          (req) async => http.Response(jsonEncode(page1()), 200));
      await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      final recs = await TxHistoryStore.list(me);
      expect(recs.where((r) => r.hash == '0xin').length, 1);
    });

    test('explorer failure leaves store and watermark untouched', () async {
      final client = MockClient(
          (req) async => http.Response('upstream exploded', 502));
      final changed = await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      expect(changed, false);
      expect(await TxHistoryStore.list(me), isEmpty);
    });

    test(
        'interrupted scan does NOT advance the watermark; the next refresh '
        'resumes and closes the gap', () async {
      // Page 1 (fresh, no cursor) succeeds; page 2 dies with a 502. The
      // old buggy behavior watermarked to head anyway, permanently hiding
      // every block below page 1's floor.
      var phase = 1;
      final client = MockClient((req) async {
        final cursor = req.url.queryParameters['cursor'];
        if (phase == 1) {
          if (cursor == null) return http.Response(jsonEncode(page1()), 200);
          return http.Response('upstream exploded', 502);
        }
        // Phase 2: the resumed refresh — MUST start from the saved floor,
        // not from head.
        expect(cursor, '70900',
            reason: 'resume must continue from the interrupted floor');
        return http.Response(
            jsonEncode({
              'address': me,
              'txs': [
                {
                  'hash': '0xdeep',
                  'from': 'anim1older',
                  'to': me,
                  'value': '7000000000',
                  'status': 'confirmed',
                  'blockNumber': 70850,
                  'timestamp': 1786480000,
                  'gasPrice': 1,
                  'gasLimit': 21000,
                },
              ],
              'nextCursor': null, // reached genesis — gap closed
              'scannedBlocks': 200,
            }),
            200);
      });

      await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      final sp = await SharedPreferences.getInstance();
      expect(sp.getInt('tx_history_scan_v1_$me'), null,
          reason: 'watermark must not advance on an interrupted scan');

      phase = 2;
      final changed = await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      expect(changed, true);
      // The deep tx below the interruption point was recovered…
      expect((await TxHistoryStore.list(me)).any((r) => r.hash == '0xdeep'),
          true);
      // …and only now does the watermark land at page 1's scan top.
      expect(sp.getInt('tx_history_scan_v1_$me'), 70900 + 250);
    });

    test('explorer-confirmed duplicate heals a dropped local record',
        () async {
      await TxHistoryStore.add(TxRecord(
        hash: '0xin',
        account: me,
        from: me,
        to: 'anim1payer',
        kind: 'transfer',
        amountNanos: BigInt.one,
        feeNanos: BigInt.from(21000),
        timestampMs: 5,
        status: TxStatus.dropped,
      ));
      final client = MockClient((req) async {
        if (req.url.queryParameters['cursor'] == null) {
          return http.Response(jsonEncode(page1()), 200);
        }
        return http.Response(
            jsonEncode({
              'address': me,
              'txs': [],
              'nextCursor': null,
              'scannedBlocks': 100
            }),
            200);
      });
      final changed = await TxHistoryStore.ingestIncoming(me,
          apiBase: 'https://x/api', client: client);
      expect(changed, true);
      final rec =
          (await TxHistoryStore.list(me)).firstWhere((r) => r.hash == '0xin');
      expect(rec.status, TxStatus.confirmed);
      expect(rec.blockHeight, 71000);
      // Richer local fields survive the heal.
      expect(rec.timestampMs, 5);
      expect(rec.to, 'anim1payer');
    });
  });
}
