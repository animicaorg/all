// L2 client request-shaping tests.
//
// A fake RpcClient records every JSON-RPC call and returns canned results, so
// we can assert the wallet signing recipe shapes prepare/submit/poll exactly
// as the node's l2_* methods expect — WITHOUT the flutter_js ML-DSA engine
// (the signer is injected) and WITHOUT a live node.

import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/constants.dart';
import 'package:animica_wallet/models/account.dart';
import 'package:animica_wallet/services/l2.dart';
import 'package:animica_wallet/services/rpc.dart';

/// Records calls and replays scripted results keyed by method name.
class FakeRpc extends RpcClient {
  FakeRpc(this._responses)
      : super(endpoints: const ['http://test.invalid/rpc']);

  final Map<String, dynamic> _responses;
  final List<({String method, dynamic params})> calls = [];

  @override
  Future<dynamic> call(String method, [dynamic params]) async {
    calls.add((method: method, params: params));
    if (!_responses.containsKey(method)) {
      throw RpcError(-32601, 'method not found: $method');
    }
    return _responses[method];
  }

  ({String method, dynamic params}) lastOf(String method) =>
      calls.lastWhere((c) => c.method == method);
}

Account _testAccount() {
  // algId MUST be 0x1003 (ML-DSA-65) — signAndSubmit refuses anything else.
  // The byte contents are irrelevant here (the signer is faked); only the
  // pubkey→address derivation and the pubkey hex matter.
  final pk = Uint8List.fromList(List<int>.generate(1952, (i) => i & 0xff));
  final sk = Uint8List.fromList(List<int>.generate(4032, (i) => (i * 7) & 0xff));
  return Account(
    label: 'test',
    algId: AnimicaConfig.algIdMlDsa65,
    publicKey: pk,
    secretKey: sk,
  );
}

/// A 64-byte signing hash, as 0x-hex, mimicking the node's sha3-512 output.
String _signingHex() =>
    '0x${List<int>.generate(64, (i) => (i + 1) & 0xff).map((b) => b.toRadixString(16).padLeft(2, '0')).join()}';

Map<String, dynamic> _preparedResponse({
  String kind = 'transfer',
  required String sender,
  required String recipient,
  Object amount = 2500000000, // 2.5 ANM in nanos
  int nonce = 3,
}) =>
    {
      'kind': kind,
      'sender': sender,
      'recipient': recipient,
      'amount': amount,
      'nonce': nonce,
      'fee': 21000,
      'requiredFee': 21000,
      'l2ChainId': 42,
      'bodyHex': '0xdeadbeef',
      'signingHash': _signingHex(),
      'sigScheme': 'ml_dsa_65',
    };

void main() {
  group('prepareTransfer request shaping', () {
    test('sends kind/sender/recipient/amount and parses the echo', () async {
      final acc = _testAccount();
      final rpc = FakeRpc({
        'l2_prepareTransfer':
            _preparedResponse(sender: acc.address, recipient: 'anim1dest'),
      });
      final l2 = L2Client(rpc);

      final prepared = await l2.prepareTransfer(
        kind: L2Kind.transfer,
        sender: acc.address,
        recipient: 'anim1dest',
        amountNanos: BigInt.from(2500000000),
        memo: 'lunch',
      );

      final params =
          rpc.lastOf('l2_prepareTransfer').params as Map<String, dynamic>;
      expect(params['kind'], 'transfer');
      expect(params['sender'], acc.address);
      expect(params['recipient'], 'anim1dest');
      expect(params['amount'], 2500000000); // int within 2^53
      expect(params['memo'], 'lunch');

      expect(prepared.nonce, 3);
      expect(prepared.fee, BigInt.from(21000));
      expect(prepared.l2ChainId, 42);
      expect(prepared.bodyHex, '0xdeadbeef');
      expect(prepared.signingHash.length, 64);
      expect(prepared.signingHash.first, 1);
    });

    test('a huge amount is sent as a decimal string, not a lossy int',
        () async {
      final acc = _testAccount();
      final huge = (BigInt.from(1) << 60); // > 2^53
      final rpc = FakeRpc({
        'l2_prepareTransfer': _preparedResponse(
            sender: acc.address, recipient: 'anim1dest', amount: huge.toString()),
      });
      final l2 = L2Client(rpc);

      await l2.prepareTransfer(
        kind: L2Kind.pay,
        sender: acc.address,
        recipient: 'anim1dest',
        amountNanos: huge,
      );
      final params =
          rpc.lastOf('l2_prepareTransfer').params as Map<String, dynamic>;
      expect(params['kind'], 'pay');
      expect(params['amount'], huge.toString());
      expect(params.containsKey('memo'), isFalse);
    });

    test('rejects a signingHash that is not 64 bytes', () async {
      final acc = _testAccount();
      final bad = _preparedResponse(sender: acc.address, recipient: 'anim1dest')
        ..['signingHash'] = '0x0102';
      final rpc = FakeRpc({'l2_prepareTransfer': bad});
      final l2 = L2Client(rpc);
      expect(
        () => l2.prepareTransfer(
          kind: L2Kind.transfer,
          sender: acc.address,
          recipient: 'anim1dest',
          amountNanos: BigInt.one,
        ),
        throwsA(isA<StateError>()),
      );
    });
  });

  group('signAndSubmit request shaping', () {
    test('signs the 64-byte hash directly and submits body/pubkey/sig hex',
        () async {
      final acc = _testAccount();
      final rpc = FakeRpc({'l2_submitSigned': '0xtxid123'});

      Uint8List? signedMessage;
      final l2 = L2Client(rpc, signer: (account, hash) async {
        signedMessage = hash;
        return Uint8List.fromList(List<int>.filled(3309, 0xAB));
      });

      final prepared = L2Prepared.fromJson(
          _preparedResponse(sender: acc.address, recipient: 'anim1dest'));
      final txid = await l2.signAndSubmit(acc, prepared);

      expect(txid, '0xtxid123');
      // The message signed is EXACTLY the node's signingHash — no re-hashing.
      expect(signedMessage, isNotNull);
      expect(signedMessage!.length, 64);
      expect(signedMessage, equals(prepared.signingHash));

      final params =
          rpc.lastOf('l2_submitSigned').params as Map<String, dynamic>;
      expect(params['body'], '0xdeadbeef');
      expect(params['pubkey'], '0x${_hex(acc.publicKey)}');
      expect(params['signature'], startsWith('0x'));
      expect((params['signature'] as String).length, 2 + 3309 * 2);
    });

    test('refuses a non-ML-DSA-65 account', () async {
      final rpc = FakeRpc({});
      final l2 = L2Client(rpc, signer: (a, h) async => Uint8List(3309));
      final legacy = Account(
        label: 'legacy',
        algId: AnimicaConfig.algIdDilithium3,
        publicKey: Uint8List(1952),
        secretKey: Uint8List(4000),
      );
      final prepared = L2Prepared.fromJson(
          _preparedResponse(sender: legacy.address, recipient: 'anim1dest'));
      expect(() => l2.signAndSubmit(legacy, prepared),
          throwsA(isA<UnsupportedError>()));
    });
  });

  group('sendInstant full recipe', () {
    test('prepare → confirm → sign → submit → poll to PROVEN', () async {
      final acc = _testAccount();
      final rpc = FakeRpc({
        'l2_prepareTransfer':
            _preparedResponse(sender: acc.address, recipient: 'anim1dest'),
        'l2_submitSigned': '0xabc',
        'l2_getTransaction': {
          'txid': '0xabc',
          'status': 'PROVEN',
          'batch': 7,
        },
      });
      var signed = false;
      final l2 = L2Client(rpc, signer: (a, h) async {
        signed = true;
        return Uint8List.fromList(List<int>.filled(3309, 1));
      });

      L2Prepared? confirmedWith;
      final statuses = <L2TxStatus>[];
      final res = await l2.sendInstant(
        account: acc,
        to: 'anim1dest',
        amountNanos: BigInt.from(2500000000),
        confirm: (p) async {
          confirmedWith = p;
          return true;
        },
        onStatus: statuses.add,
        pollInterval: Duration.zero,
        maxPolls: 3,
      );

      expect(signed, isTrue);
      expect(confirmedWith, isNotNull);
      expect(confirmedWith!.recipient, 'anim1dest');
      expect(res, isNotNull);
      expect(res!.txid, '0xabc');
      expect(res.status, L2TxStatus.proven);
      expect(res.status.isProven, isTrue);
      expect(statuses, contains(L2TxStatus.proven));

      // Order of calls: prepare, submit, then at least one getTransaction.
      final methods = rpc.calls.map((c) => c.method).toList();
      expect(methods.first, 'l2_prepareTransfer');
      expect(methods, contains('l2_submitSigned'));
      expect(methods, contains('l2_getTransaction'));
      expect(methods.indexOf('l2_submitSigned'),
          lessThan(methods.indexOf('l2_getTransaction')));
    });

    test('a declined confirmation signs and submits nothing', () async {
      final acc = _testAccount();
      final rpc = FakeRpc({
        'l2_prepareTransfer':
            _preparedResponse(sender: acc.address, recipient: 'anim1dest'),
      });
      var signed = false;
      final l2 = L2Client(rpc, signer: (a, h) async {
        signed = true;
        return Uint8List(3309);
      });

      final res = await l2.sendInstant(
        account: acc,
        to: 'anim1dest',
        amountNanos: BigInt.one,
        confirm: (p) async => false,
      );

      expect(res, isNull);
      expect(signed, isFalse);
      expect(rpc.calls.map((c) => c.method), isNot(contains('l2_submitSigned')));
    });
  });

  group('withdrawToL1', () {
    test('defaults the L1 payout to the account address and uses kind=withdraw',
        () async {
      final acc = _testAccount();
      final rpc = FakeRpc({
        'l2_prepareTransfer': _preparedResponse(
            kind: 'withdraw', sender: acc.address, recipient: acc.address),
        'l2_submitSigned': '0xwd',
        'l2_getTransaction': {'txid': '0xwd', 'status': 'SOFT_CONFIRMED'},
      });
      final l2 = L2Client(rpc, signer: (a, h) async => Uint8List(3309));

      final res = await l2.withdrawToL1(
        account: acc,
        amountNanos: BigInt.from(1000000000),
        pollInterval: Duration.zero,
        maxPolls: 1,
      );

      final params =
          rpc.lastOf('l2_prepareTransfer').params as Map<String, dynamic>;
      expect(params['kind'], 'withdraw');
      expect(params['recipient'], acc.address); // defaulted to own L1 address
      // SOFT_CONFIRMED is NOT terminal, so polling exhausts maxPolls and the
      // last-seen status is surfaced without being mistaken for settlement.
      expect(res!.status, L2TxStatus.softConfirmed);
      expect(res.status.isProven, isFalse);
    });
  });
}

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
