// Golden vectors for the canonical tx signing path — the bytes the CHAIN
// verifies against.
//
// Every hex constant below was produced by the NODE'S OWN Python
// (`core.utils.tx.normalize_tx_body`, `animica.tx.signing.tx_signing_preimage`,
// `core.encoding.cbor.dumps`, `pq.py.sign.build_sign_bytes`), not by a
// re-implementation. Regenerate them with the committed generator:
//
//   cd /root/animica && .venv/bin/python \
//       apps/wallet-mobile-flutter/test/golden/gen_tx_vectors.py
//
// WHY THIS FILE EXISTS
//
// The node verifies a tx signature over exactly ONE message and has no
// fallback (rpc/methods/tx.py `_verify_pq_signature` → the single
// `animica.tx.v1.preimage` candidate). That message is
//
//   sha3_512(build_sign_bytes(msg = canonical_cbor({
//       1: "animica.tx.v1", 2: chainId, 3: genesisHash, 4: network,
//       5: "tx", 6: txVersion, 7: normalize_tx_body(body)}), …))
//
// The wallet used to sign `canonical_cbor(body)` — no wrapper, no genesis, no
// network — so EVERY mobile transaction was rejected. Worse, the node puts
// `normalize_tx_body(body)` at key 7, and `normalize_tx_body` REWRITES any
// body that is not already `{v, gas, payload, …}`, hardcoding `payload.t = 0`.
// So even "just wrap the flat body in a preimage" still fails, and contract
// calls were being silently turned into transfers. Both failures are asserted
// below so neither can come back.

import 'dart:convert';
import 'dart:typed_data';

import 'package:animica_wallet/services/canonical.dart';
import 'package:animica_wallet/services/deploy.dart';
import 'package:animica_wallet/services/signer.dart';
import 'package:flutter_test/flutter_test.dart';

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

Uint8List _bytes(String hex) {
  final out = Uint8List(hex.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}

// ── fixture ────────────────────────────────────────────────────────────
// address_from_pubkey(b"\x11" * 1952, 0x1003) / (b"\x22" * 1952, 0x1003)
const String kFrom =
    'anim1zqpszd0dlq4ukh682fvrpx2lv6wmwxnw0lq3rq3y9lppe07e05utuqsxjd23s';
const String kTo =
    'anim1zqpmxapvv6pf5qlrecetx5604k4u4gcf25vgyh4a9z4ucyxhhrvqe8cw58wa6';
const String kGenesisHex =
    '8f2c1d4b6a90e3f57c81b24d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f21';
const int kNonce = 7;
const int kAmount = 1234;
const int kAlgId = 0x1003; // ML-DSA-65

// The App Store's on-chain purchase memo, carried in a transfer's `data`.
const String kMemo = 'ANMSTORE1{"v":1,"pid":"pur_abc123","b":"anim1source",'
    '"l":"lst_x","a":1234,"n":"00112233445566778899aabbccddeeff"}';

const String kCalldataHex = 'a9059cbb'
    '3333333333333333333333333333333333333333333333333333333333333333'
    '0000000000000000000000000000000000000000000000000000000005f5e100';

const String kCode = "# animica python-vm package\nprint('hi')\n";
const String kManifest = '{"name":"demo","version":"1.0.0","entry":"main"}';

/// Mainnet's chain-identity payload has NO `network` key, so the node's own
/// fallback (`network or name or "unknown"`) is what actually gets signed.
final AnimicaChainContext kCtx = AnimicaChainContext(
  chainId: 1,
  genesisHash: _bytes(kGenesisHex),
  network: 'unknown',
  forkId: null,
);

class TxVector {
  final String bodyCbor;
  final String preimage;
  final String preimageSha3_512;
  final String signBytes;
  const TxVector({
    required this.bodyCbor,
    required this.preimage,
    required this.preimageSha3_512,
    required this.signBytes,
  });
}

const TxVector kTransfer = TxVector(
  bodyCbor: 'a761760163676173a2656c696d6974195208657072696365016466726f6d5820'
      '0135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd97d38be02'
      '656e6f6e63650767636861696e496401677061796c6f6164a26174006176a362'
      '746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28abcc10d7'
      'b8d80c9f64646174614066616d6f756e741904d26a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d6974195208657072696365016466'
      '726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbf'
      'd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a26174'
      '006176a362746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd'
      '28abcc10d7b8d80c9f64646174614066616d6f756e741904d26a616363657373'
      '4c69737480',
  preimageSha3_512:
      'ab8d50bf0c959be55e4ea69ee6e8bf3723b5bc87d7c5513006c4e8d1214b59c1'
          'e46af6591e833f476916815081f011b0f2a48b2121270902f6422d2944249796',
  signBytes: '7bcc0f665914858c04af55bdf5df073e60bc55d057a5d21b2fa4f13b44f38388'
      'a8309345eb323cd0cd8a16af8d6a1fdd99233dab8509332081357ce414723517',
);

const TxVector kTransferMemo = TxVector(
  bodyCbor: 'a761760163676173a2656c696d6974195208657072696365016466726f6d5820'
      '0135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd97d38be02'
      '656e6f6e63650767636861696e496401677061796c6f6164a26174006176a362'
      '746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28abcc10d7'
      'b8d80c9f64646174615871414e4d53544f5245317b2276223a312c2270696422'
      '3a227075725f616263313233222c2262223a22616e696d31736f75726365222c'
      '226c223a226c73745f78222c2261223a313233342c226e223a22303031313232'
      '3333343435353636373738383939616162626363646465656666227d66616d6f'
      '756e741904d26a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d6974195208657072696365016466'
      '726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbf'
      'd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a26174'
      '006176a362746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd'
      '28abcc10d7b8d80c9f64646174615871414e4d53544f5245317b2276223a312c'
      '22706964223a227075725f616263313233222c2262223a22616e696d31736f75'
      '726365222c226c223a226c73745f78222c2261223a313233342c226e223a2230'
      '3031313232333334343535363637373838393961616262636364646565666622'
      '7d66616d6f756e741904d26a6163636573734c69737480',
  preimageSha3_512:
      'f050733e1d06e671ea932a69d002e953f1012d92ab738f5f454e7624d36a5f25'
          '20d08a2c1be4e995267df7f38623253aede2175a7b791e56a027ef1a2ddd6ce0',
  signBytes: '9384fbbbef934d9d6494ac2c4de7d38b2495661be068e1dc9209a337de0328aa'
      'a784275e4a286a143c7405182b66d0721cae79c93b1c3424f4305b21b66fda95',
);

const TxVector kCall = TxVector(
  bodyCbor: 'a761760163676173a2656c696d69741a00030d40657072696365016466726f6d'
      '58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd97d38'
      'be02656e6f6e63650767636861696e496401677061796c6f6164a26174026176'
      'a262746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28abcc'
      '10d7b8d80c9f64646174615844a9059cbb333333333333333333333333333333'
      '3333333333333333333333333333333333000000000000000000000000000000'
      '0000000000000000000000000005f5e1006a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741a00030d4065707269636501'
      '6466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc2'
      '1cbfd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a2'
      '6174026176a262746f5820b3742c66829a03e3ce32b3534fadabcaa309551882'
      '5ebd28abcc10d7b8d80c9f64646174615844a9059cbb33333333333333333333'
      '3333333333333333333333333333333333333333333300000000000000000000'
      '00000000000000000000000000000000000005f5e1006a6163636573734c6973'
      '7480',
  preimageSha3_512:
      '3598678788f624192f733fb29e2ec01ac42f2158769a4444012c8c22d1d69418'
          '66f567c189428533401207a69e957cb0694e6d0e815b6ef69dce1393779ee3bb',
  signBytes: 'be5a50ad357afcc52c6b43c10ea256ca4d575f12ef1f5420de28c55481a0a249'
      '135b94b00276370b56685eeeef30a7d7397c99a59986d5d4d300c02ed8c0303c',
);

const TxVector kDeploy = TxVector(
  bodyCbor: 'a761760163676173a2656c696d69741a001e8480657072696365016466726f6d'
      '58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd97d38'
      'be02656e6f6e63650767636861696e496401677061796c6f6164a26174016176'
      'a264636f646558282320616e696d69636120707974686f6e2d766d207061636b'
      '6167650a7072696e742827686927290a686d616e696665737458307b226e616d'
      '65223a2264656d6f222c2276657273696f6e223a22312e302e30222c22656e74'
      '7279223a226d61696e227d6a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741a001e848065707269636501'
      '6466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc2'
      '1cbfd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a2'
      '6174016176a264636f646558282320616e696d69636120707974686f6e2d766d'
      '207061636b6167650a7072696e742827686927290a686d616e69666573745830'
      '7b226e616d65223a2264656d6f222c2276657273696f6e223a22312e302e3022'
      '2c22656e747279223a226d61696e227d6a6163636573734c69737480',
  preimageSha3_512:
      'b778dc7953e3c559e689c8d0d68b95e803600478b9ffbea83a81f978cf94d453'
          '47bc95e785879d622230c5dcd2f1c9a079ee5ff4d0f93f612ddff273fa30f75b',
  signBytes: 'b13a8bdd806d9a021753dfb41206ba51d5a72fc66374237cacd0952dadf907ff'
      'f322dea353dfff5f06535364968e0555b7dd9db6b8372d86b2bc6f8ef74810e7',
);

/// Assert the FULL chain of bytes for one tx kind: body CBOR → signing
/// preimage → sha3-512 of the preimage → the prehash ML-DSA-65 actually signs.
void _expectVector(String label, Map<String, dynamic> body, TxVector v) {
  final bodyCbor = canonicalCbor(body);
  expect(_hex(bodyCbor), v.bodyCbor, reason: '$label: body CBOR');

  final preimage = txSigningPreimage(body, kCtx);
  expect(_hex(preimage), v.preimage, reason: '$label: signing preimage');

  expect(_hex(sha3_512(preimage)), v.preimageSha3_512,
      reason: '$label: sha3-512(preimage)');

  final prehash = buildSignBytes(
    msg: preimage,
    algId: kAlgId,
    chainId: kCtx.chainId,
    forkId: kCtx.forkId,
  );
  expect(_hex(prehash), v.signBytes, reason: '$label: build_sign_bytes');
}

void main() {
  group('golden vectors vs. the node\'s own Python', () {
    test('transfer (t=0, empty data)', () {
      _expectVector(
        'transfer',
        buildTransferBody(
          from: kFrom,
          to: kTo,
          amountNanos: BigInt.from(kAmount),
          nonce: kNonce,
        ),
        kTransfer,
      );
    });

    test('transfer with an ANMSTORE1 memo (t=0, data)', () {
      _expectVector(
        'transfer_memo',
        buildTransferBody(
          from: kFrom,
          to: kTo,
          amountNanos: BigInt.from(kAmount),
          nonce: kNonce,
          data: Uint8List.fromList(utf8.encode(kMemo)),
        ),
        kTransferMemo,
      );
    });

    test('contract call (t=2)', () {
      _expectVector(
        'call',
        buildCallBody(
          from: kFrom,
          to: kTo,
          calldata: _bytes(kCalldataHex),
          nonce: kNonce,
        ),
        kCall,
      );
    });

    test('deploy (t=1)', () {
      _expectVector(
        'deploy',
        buildDeployBody(
          from: kFrom,
          code: Uint8List.fromList(utf8.encode(kCode)),
          manifest: Uint8List.fromList(utf8.encode(kManifest)),
          nonce: kNonce,
        ),
        kDeploy,
      );
    });

    test('the call body is a REAL call, not a transfer wearing a data field',
        () {
      // normalize_tx_body maps any flat body to payload.t = 0, so before this
      // change `buildCallBody` produced a transfer. Assert the tag directly.
      final call = buildCallBody(
        from: kFrom,
        to: kTo,
        calldata: _bytes(kCalldataHex),
        nonce: kNonce,
      );
      expect((call['payload'] as Map)['t'], 2);
      expect(
          ((call['payload'] as Map)['v'] as Map).keys.toSet(), {'to', 'data'});

      final transfer = buildTransferBody(
        from: kFrom,
        to: kTo,
        amountNanos: BigInt.from(kAmount),
        nonce: kNonce,
      );
      expect((transfer['payload'] as Map)['t'], 0);

      final deploy = buildDeployBody(
        from: kFrom,
        code: Uint8List.fromList(utf8.encode(kCode)),
        manifest: Uint8List.fromList(utf8.encode(kManifest)),
        nonce: kNonce,
      );
      expect((deploy['payload'] as Map)['t'], 1);
    });

    test('addresses are the raw 32-byte bech32m digest (_pad_addr parity)', () {
      final body = buildTransferBody(
        from: kFrom,
        to: kTo,
        amountNanos: BigInt.one,
        nonce: 0,
      );
      final from = body['from'] as Uint8List;
      final to = ((body['payload'] as Map)['v'] as Map)['to'] as Uint8List;
      expect(from.length, 32);
      expect(to.length, 32);
      // Same digests that appear inside the golden body CBOR above.
      expect(_hex(from),
          '0135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd97d38be02');
      expect(_hex(to),
          'b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28abcc10d7b8d80c9f');
    });
  });

  group('the two proven traps stay closed', () {
    test('signing the bare body CBOR is NOT the message the node verifies', () {
      final body = buildTransferBody(
        from: kFrom,
        to: kTo,
        amountNanos: BigInt.from(kAmount),
        nonce: kNonce,
      );
      // The old wallet signed exactly this. It is a different byte string
      // from the preimage, which is why every mobile tx was rejected.
      expect(_hex(canonicalCbor(body)),
          isNot(equals(_hex(txSigningPreimage(body, kCtx)))));
    });

    test('a flat legacy body cannot be signed at all', () {
      // Wrapping a FLAT body in the preimage does not help: the node puts
      // normalize_tx_body(body) at key 7, and for a flat body that is a
      // different map. Rather than emit a preimage that silently disagrees
      // with the node's, refuse.
      final flat = <String, dynamic>{
        'to': kTo,
        'data': Uint8List(0),
        'from': kFrom,
        'nonce': kNonce,
        'value': kAmount,
        'maxFee': 1000000000,
        'chainId': 1,
        'gasLimit': 21000,
      };
      expect(() => txSigningPreimage(flat, kCtx), throwsArgumentError);
      expect(() => assertCanonicalBody(flat), throwsArgumentError);
    });
  });

  group('payable call amount', () {
    // FORK_VALUE_CALL: a CALL may carry ANM. The canonical form OMITS `amount`
    // when zero so a value-less call's preimage/txid stays byte-identical to
    // every pre-fork call; a positive value adds `amount`.
    Map<String, dynamic> callV(Map<String, dynamic> body) =>
        (body['payload'] as Map)['v'] as Map<String, dynamic>;

    test('a positive value is carried as payload.v.amount', () {
      final body = buildCallBody(
        from: kFrom,
        to: kTo,
        calldata: _bytes(kCalldataHex),
        nonce: kNonce,
        value: BigInt.from(5),
      );
      expect(callV(body)['amount'], BigInt.from(5));
    });

    test('zero / null value OMITS amount (byte-identical to a valueless call)', () {
      final zero = buildCallBody(
        from: kFrom, to: kTo, calldata: _bytes(kCalldataHex), nonce: kNonce, value: BigInt.zero);
      final none = buildCallBody(
        from: kFrom, to: kTo, calldata: _bytes(kCalldataHex), nonce: kNonce);
      expect(callV(zero).containsKey('amount'), isFalse);
      expect(callV(none).containsKey('amount'), isFalse);
    });

    test('a negative value is rejected', () {
      expect(
        () => buildCallBody(
          from: kFrom,
          to: kTo,
          calldata: _bytes(kCalldataHex),
          nonce: kNonce,
          value: BigInt.from(-1),
        ),
        throwsArgumentError,
      );
    });

    test('a deploy carrying ANM is rejected', () {
      expect(
        () => buildDeployBody(
          from: kFrom,
          code: Uint8List.fromList(utf8.encode(kCode)),
          manifest: Uint8List.fromList(utf8.encode(kManifest)),
          nonce: kNonce,
          value: BigInt.one,
        ),
        throwsArgumentError,
      );
    });

    test('an empty-calldata "call" is rejected (TxCall.data must be non-empty)',
        () {
      expect(
        () => buildCallBody(
          from: kFrom,
          to: kTo,
          calldata: Uint8List(0),
          nonce: kNonce,
        ),
        throwsArgumentError,
      );
    });
  });

  group('transfer memo guardrail', () {
    test('rejects >1024 bytes, accepts exactly 1024', () {
      expect(
        () => buildTransferBody(
          from: kFrom,
          to: kTo,
          amountNanos: BigInt.one,
          nonce: 0,
          data: Uint8List(kMaxTransferDataBytes + 1),
        ),
        throwsArgumentError,
      );
      expect(
        () => buildTransferBody(
          from: kFrom,
          to: kTo,
          amountNanos: BigInt.one,
          nonce: 0,
          data: Uint8List(kMaxTransferDataBytes),
        ),
        returnsNormally,
      );
    });
  });

  group('parseDeployBytes', () {
    test('decodes a JSON.stringify\'d Uint8Array (numeric-key object)', () {
      // What `JSON.stringify(new Uint8Array([1,2,255]))` produces, which is
      // what the browser bridge hands the Flutter handler.
      final decoded =
          parseDeployBytes(<String, dynamic>{'0': 1, '1': 2, '2': 255});
      expect(decoded, isNotNull);
      expect(_hex(decoded!), '0102ff');
      expect(parseDeployBytes(<String, dynamic>{}), isEmpty);
    });

    test('rejects an object that is not a contiguous 0..n-1 index map', () {
      expect(parseDeployBytes(<String, dynamic>{'0': 1, '2': 2}), isNull);
      expect(parseDeployBytes(<String, dynamic>{'0': 1, '1': 999}), isNull);
      expect(parseDeployBytes(<String, dynamic>{'a': 1}), isNull);
      expect(parseDeployBytes(<String, dynamic>{'00': 1}), isNull);
    });

    test('the 0x prefix is case-insensitive', () {
      expect(_hex(parseDeployBytes('0XdeadBEEF')!), 'deadbeef');
      expect(_hex(parseDeployBytes('0xdeadbeef')!), 'deadbeef');
      expect(parseDeployBytes('0X'), isEmpty);
    });

    test('malformed hex is an error, never UTF-8 contract code', () {
      // Odd length and a stray non-hex digit both used to fall through to
      // "encode the literal string as bytecode".
      expect(parseDeployBytes('0xabc'), isNull);
      expect(parseDeployBytes('0xzz'), isNull);
      expect(parseDeployBytes('0Xabc'), isNull);
    });

    test('plain text is still UTF-8 encoded (manifests pasted as text)', () {
      expect(_hex(parseDeployBytes('{"name":"x"}')!),
          _hex(Uint8List.fromList(utf8.encode('{"name":"x"}'))));
      expect(parseDeployBytes(<int>[1, 2, 3], ), isNotNull);
      expect(parseDeployBytes(42), isNull);
      expect(parseDeployBytes(null), isNull);
    });
  });
}
