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
  bodyCbor: 'a761760163676173a2656c696d69741952086570726963651a3b9aca00646672'
      '6f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd9'
      '7d38be02656e6f6e63650767636861696e496401677061796c6f6164a2617400'
      '6176a362746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28'
      'abcc10d7b8d80c9f64646174614066616d6f756e741904d26a6163636573734c'
      '69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741952086570726963651a3b9a'
      'ca006466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc1118224'
      '2fc21cbfd97d38be02656e6f6e63650767636861696e496401677061796c6f61'
      '64a26174006176a362746f5820b3742c66829a03e3ce32b3534fadabcaa30955'
      '18825ebd28abcc10d7b8d80c9f64646174614066616d6f756e741904d26a6163'
      '636573734c69737480',
  preimageSha3_512:
      'c575f45484487532729e05e0bdc4e2cacc0aa1da2acb14c59936ccbc026be9c0'
          '199eaba94457e0e2b8bea91a00bd085a5449ce1bf21e41834b975b7ff205220d',
  signBytes: '88945dbef9037d87325ff6064df1cccac824540c89d2e318b617e5dfc374760f'
      '7c6a54c8ecac146194760c06e55de1bbae534f932d43f3848c5a73077e20005d',
);

const TxVector kTransferMemo = TxVector(
  bodyCbor: 'a761760163676173a2656c696d69741952086570726963651a3b9aca00646672'
      '6f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21cbfd9'
      '7d38be02656e6f6e63650767636861696e496401677061796c6f6164a2617400'
      '6176a362746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825ebd28'
      'abcc10d7b8d80c9f64646174615871414e4d53544f5245317b2276223a312c22'
      '706964223a227075725f616263313233222c2262223a22616e696d31736f7572'
      '6365222c226c223a226c73745f78222c2261223a313233342c226e223a223030'
      '313132323333343435353636373738383939616162626363646465656666227d'
      '66616d6f756e741904d26a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741952086570726963651a3b9a'
      'ca006466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc1118224'
      '2fc21cbfd97d38be02656e6f6e63650767636861696e496401677061796c6f61'
      '64a26174006176a362746f5820b3742c66829a03e3ce32b3534fadabcaa30955'
      '18825ebd28abcc10d7b8d80c9f64646174615871414e4d53544f5245317b2276'
      '223a312c22706964223a227075725f616263313233222c2262223a22616e696d'
      '31736f75726365222c226c223a226c73745f78222c2261223a313233342c226e'
      '223a223030313132323333343435353636373738383939616162626363646465'
      '656666227d66616d6f756e741904d26a6163636573734c69737480',
  preimageSha3_512:
      '34d5c20ec592eded9262a99f5ea25fe77e36214a6446c8c6a4a894fe485df7b7'
          '4089373f8945f200ec7e76522ba6b0c43c51752c9db139e2805f4a984ff51d72',
  signBytes: '6d7c4b422be275712993c9db169a3e67454b534517ef842d8c9199e29cd5271f'
      '91150dae74e58fbdcc4a7647ae031aee69eed127f88946925021c743c50cc24e',
);

const TxVector kCall = TxVector(
  bodyCbor: 'a761760163676173a2656c696d69741a00030d406570726963651a3b9aca0064'
      '66726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21c'
      'bfd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a261'
      '74026176a262746f5820b3742c66829a03e3ce32b3534fadabcaa3095518825e'
      'bd28abcc10d7b8d80c9f64646174615844a9059cbb3333333333333333333333'
      '3333333333333333333333333333333333333333330000000000000000000000'
      '000000000000000000000000000000000005f5e1006a6163636573734c697374'
      '80',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741a00030d406570726963651a'
      '3b9aca006466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc111'
      '82242fc21cbfd97d38be02656e6f6e63650767636861696e496401677061796c'
      '6f6164a26174026176a262746f5820b3742c66829a03e3ce32b3534fadabcaa3'
      '095518825ebd28abcc10d7b8d80c9f64646174615844a9059cbb333333333333'
      '3333333333333333333333333333333333333333333333333333000000000000'
      '0000000000000000000000000000000000000000000005f5e1006a6163636573'
      '734c69737480',
  preimageSha3_512:
      '75d15f9cda92dc0c6ca99a42147466812534f6b4c85785ddf8242c27f7dccbd6'
          '487ffa175426ce0f49dbe8b773d9ee6be6225a29775d61dc026cddce10ee55ce',
  signBytes: '6a70f92c32f828768a102654b1dafd14518980a3c417065c5d0650955e5fae6e'
      '1ac72cdd1d3357f97f573311f9f0c3e9ada3197f8be94a8d3832fe05fab8364d',
);

const TxVector kDeploy = TxVector(
  bodyCbor: 'a761760163676173a2656c696d69741a001e84806570726963651a3b9aca0064'
      '66726f6d58200135edf82bcb5f47525830995f669db71a6e7fc11182242fc21c'
      'bfd97d38be02656e6f6e63650767636861696e496401677061796c6f6164a261'
      '74016176a264636f646558282320616e696d69636120707974686f6e2d766d20'
      '7061636b6167650a7072696e742827686927290a686d616e696665737458307b'
      '226e616d65223a2264656d6f222c2276657273696f6e223a22312e302e30222c'
      '22656e747279223a226d61696e227d6a6163636573734c69737480',
  preimage: 'a7016d616e696d6963612e74782e763102010358208f2c1d4b6a90e3f57c81b2'
      '4d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f210467756e6b6e6f776e0562'
      '7478060107a761760163676173a2656c696d69741a001e84806570726963651a'
      '3b9aca006466726f6d58200135edf82bcb5f47525830995f669db71a6e7fc111'
      '82242fc21cbfd97d38be02656e6f6e63650767636861696e496401677061796c'
      '6f6164a26174016176a264636f646558282320616e696d69636120707974686f'
      '6e2d766d207061636b6167650a7072696e742827686927290a686d616e696665'
      '737458307b226e616d65223a2264656d6f222c2276657273696f6e223a22312e'
      '302e30222c22656e747279223a226d61696e227d6a6163636573734c69737480',
  preimageSha3_512:
      '9cf745d98c1e2db045d2966ee18ce2e2ee68f5835ff415554ddf3bdfea222dc1'
          'bd8e63a18dc16ff0a5651f804104ab25563b5ad7f611d7874600b45138c16c9a',
  signBytes: 'ac86254f6fe35dad0fa99746d1f92f2747b3d3428600be25ca58384aa25338ef'
      '805f39c582810b7e46d8f4b3e66b0e090de5443b0888bb0bf994c3d43b557e0b',
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

  group('value never disappears', () {
    test('a call carrying ANM is rejected, not silently downgraded', () {
      expect(
        () => buildCallBody(
          from: kFrom,
          to: kTo,
          calldata: _bytes(kCalldataHex),
          nonce: kNonce,
          value: BigInt.from(5),
        ),
        throwsArgumentError,
      );
      // Explicit zero is fine.
      expect(
        () => buildCallBody(
          from: kFrom,
          to: kTo,
          calldata: _bytes(kCalldataHex),
          nonce: kNonce,
          value: BigInt.zero,
        ),
        returnsNormally,
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
