// Golden-vector tests for the canonical CBOR ENCODER + build_sign_bytes.
//
// The hex values here are produced by the Python reference
// (`pq.py.sign.build_sign_bytes` + `omni_sdk.tx.signing.canonical_body_dict`)
// for known inline maps. They pin the ENCODER — key ordering, integer widths,
// bstr framing — independently of what shape the tx builders emit.
//
// The builders themselves (and the signing preimage the chain verifies) are
// covered by test/tx_golden_vectors_test.dart. Note the flat
// `{to,data,from,…}` maps below are NOT what buildTransferBody produces any
// more: they are the shape `normalize_tx_body` REWRITES, which is exactly why
// signing them never verified. They survive here purely as encoder fixtures.

import 'dart:convert';
import 'dart:typed_data';

import 'package:animica_wallet/services/canonical.dart';
import 'package:flutter_test/flutter_test.dart';

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

void main() {
  test('canonicalCbor matches Python omni_sdk for a transfer body', () {
    // From test_cli_sign_bytes_match_sdk_helper in
    // python/animica/cli/tests/test_pq_signing_alignment.py:
    //   transfer(from='anim1source', to='anim1dest', amount=1234, nonce=1,
    //            gas_limit=21000, max_fee=1_000_000_000, chain_id=1)
    // → CBOR(canonical_body_dict(tx)) ==
    //   "a862746f69616e696d31646573746464617461406466726f6d6b616e696d31736f"
    //   "75726365656e6f6e6365016576616c75651904d2666d61784665651a3b9aca0067"
    //   "636861696e496401686761734c696d6974195208"

    final body = <String, dynamic>{
      'to': 'anim1dest',
      'data': Uint8List(0),
      'from': 'anim1source',
      'nonce': 1,
      'value': 1234,
      'maxFee': 1000000000,
      'chainId': 1,
      'gasLimit': 21000,
    };

    final encoded = canonicalCbor(body);
    expect(
      _hex(encoded),
      'a862746f69616e696d31646573746464617461406466726f6d6b616e696d31736f'
      '75726365656e6f6e6365016576616c75651904d2666d61784665651a3b9aca0067'
      '636861696e496401686761734c696d6974195208',
    );
  });

  test('canonicalCbor matches Python omni_sdk for a transfer body WITH data', () {
    // Same fixed transfer body as the empty-data case above, but carrying an
    // ANMSTORE1 App Store purchase memo in the `data` bstr. The golden hex was
    // produced by RUNNING the Python reference (the exact path the chain +
    // scripts/e2e_pay_intent.py sign over):
    //
    //   from omni_sdk.tx.build import make_tx
    //   from animica.tx.signing import build_signable_tx_bytes
    //   memo = b'ANMSTORE1{"v":1,"pid":"pur_abc123","b":"anim1source",'
    //          b'"l":"lst_x","a":1234,"n":"00112233445566778899aabbccddeeff"}'
    //   tx = make_tx(from_addr='anim1source', to='anim1dest', nonce=1,
    //                value=1234, data=memo, gas_limit=21000,
    //                max_fee=1_000_000_000, chain_id=1)
    //   build_signable_tx_bytes(tx).hex()
    //
    // If the Dart `data` threading or the CBOR bstr encoding drifts from the
    // Python builder, this breaks and store payments would be rejected on-chain.
    const memoStr =
        'ANMSTORE1{"v":1,"pid":"pur_abc123","b":"anim1source",'
        '"l":"lst_x","a":1234,"n":"00112233445566778899aabbccddeeff"}';
    final memoBytes = Uint8List.fromList(utf8.encode(memoStr));

    const expectedHex =
        'a862746f69616e696d316465737464646174615871414e4d53544f5245317b22'
        '76223a312c22706964223a227075725f616263313233222c2262223a22616e69'
        '6d31736f75726365222c226c223a226c73745f78222c2261223a313233342c22'
        '6e223a223030313132323333343435353636373738383939616162626363646465'
        '656666227d6466726f6d6b616e696d31736f75726365656e6f6e636501657661'
        '6c75651904d2666d61784665651a3b9aca0067636861696e496401686761734c'
        '696d6974195208';

    // Raw encoder over an inline body map: a bstr of that length must be
    // framed and ordered exactly like this.
    final inlineBody = <String, dynamic>{
      'to': 'anim1dest',
      'data': memoBytes,
      'from': 'anim1source',
      'nonce': 1,
      'value': 1234,
      'maxFee': 1000000000,
      'chainId': 1,
      'gasLimit': 21000,
    };
    expect(_hex(canonicalCbor(inlineBody)), expectedHex);
  });

  test('map keys sort by their ENCODED bytes, ints before text', () {
    // The signing preimage is keyed 1..7 (ints). A `toString()` comparison
    // happens to work for single digits and then silently mis-orders at 10,
    // which would break every signature the day the preimage grew a key.
    final ints = <int, dynamic>{10: 'j', 2: 'b', 1: 'a'};
    expect(_hex(canonicalCbor(ints)), 'a30161610261620a616a');
    // int keys (major 0) encode below text keys (major 3).
    final mixed = <Object, dynamic>{'a': 1, 1: 2};
    expect(_hex(canonicalCbor(mixed)), 'a20102616101');
    // Text keys: shorter first, then bytewise.
    final text = <String, dynamic>{'bb': 1, 'b': 2, 'a': 3};
    expect(_hex(canonicalCbor(text)), 'a361610361620262626201');
  });

  test('integers wider than 64 bits use the CBOR bignum tags', () {
    // core/encoding/cbor.py switches to tag 2 / tag 3 above 2^64-1; the old
    // Dart encoder silently truncated to the low 8 bytes, which would have
    // signed a DIFFERENT amount than the one displayed.
    final big = BigInt.parse('18446744073709551616'); // 2^64
    expect(_hex(canonicalCbor(big)), 'c249010000000000000000');
    expect(_hex(canonicalCbor(BigInt.parse('18446744073709551615'))),
        '1bffffffffffffffff');
    // -2^64 still fits the 64-bit negative form (major 1 encodes -1-n).
    expect(_hex(canonicalCbor(-big)), '3bffffffffffffffff');
    expect(_hex(canonicalCbor(-big - BigInt.one)), 'c349010000000000000000');
  });

  test('duplicate map keys are rejected rather than emitted', () {
    expect(() => canonicalCbor(<Object, dynamic>{1: 'a', BigInt.one: 'b'}),
        throwsArgumentError);
  });

  test('uvarint encodes small + large values correctly', () {
    expect(_hex(uvarint(0)), '00');
    expect(_hex(uvarint(127)), '7f');
    expect(_hex(uvarint(128)), '8001');
    expect(_hex(uvarint(300)), 'ac02');
  });

  test('buildSignBytes is deterministic + 64 bytes', () {
    final msg = Uint8List.fromList(List<int>.generate(32, (i) => i));
    final a = buildSignBytes(msg: msg, algId: 0x1002, chainId: 1);
    final b = buildSignBytes(msg: msg, algId: 0x1002, chainId: 1);
    expect(a.length, 64);
    expect(_hex(a), _hex(b));
  });
}
