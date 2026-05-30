// Golden-vector tests for the canonical CBOR encoder + build_sign_bytes.
//
// The hex values here are produced by the Python reference
// (`pq.py.sign.build_sign_bytes` + `omni_sdk.tx.signing.canonical_body_dict`)
// for a known transfer body. If either changes, this test breaks and
// the wallet's broadcast envelopes will be rejected by the chain.

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
