// Golden-vector tests for the canonical CBOR encoder + build_sign_bytes.
//
// The hex values here are produced by the Python reference
// (`pq.py.sign.build_sign_bytes` + `omni_sdk.tx.signing.canonical_body_dict`)
// for a known transfer body. If either changes, this test breaks and
// the wallet's broadcast envelopes will be rejected by the chain.

import 'dart:convert';
import 'dart:typed_data';

import 'package:animica_wallet/services/canonical.dart';
import 'package:animica_wallet/services/signer.dart';
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

    // (1) Raw encoder over an inline body map (the true wire bytes).
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

    // (2) The production builder must thread `data` into that exact byte string.
    final builtBody = buildTransferBody(
      from: 'anim1source',
      to: 'anim1dest',
      amountNanos: BigInt.from(1234),
      nonce: 1,
      data: memoBytes,
    );
    expect(_hex(canonicalCbor(builtBody)), expectedHex);
  });

  test('buildTransferBody without data is byte-identical to the empty vector',
      () {
    // The default (data omitted) MUST reproduce the historical empty-data
    // golden vector exactly — no behavioral change for existing sends.
    final body = buildTransferBody(
      from: 'anim1source',
      to: 'anim1dest',
      amountNanos: BigInt.from(1234),
      nonce: 1,
    );
    expect(
      _hex(canonicalCbor(body)),
      'a862746f69616e696d31646573746464617461406466726f6d6b616e696d31736f'
      '75726365656e6f6e6365016576616c75651904d2666d61784665651a3b9aca0067'
      '636861696e496401686761734c696d6974195208',
    );
  });

  test('buildTransferBody rejects an oversized data payload (>1024 bytes)', () {
    final tooBig = Uint8List(kMaxTransferDataBytes + 1);
    expect(
      () => buildTransferBody(
        from: 'anim1source',
        to: 'anim1dest',
        amountNanos: BigInt.from(1),
        nonce: 0,
        data: tooBig,
      ),
      throwsArgumentError,
    );
    // Exactly at the limit is allowed.
    expect(
      () => buildTransferBody(
        from: 'anim1source',
        to: 'anim1dest',
        amountNanos: BigInt.from(1),
        nonce: 0,
        data: Uint8List(kMaxTransferDataBytes),
      ),
      returnsNormally,
    );
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
