// Known-answer tests for the vendored ML-DSA-65 bundle.
//
// The bundle was PATCHED to remove every BigInt use, because the QuickJS build shipped
// by flutter_js has no BigInt at all (constructor absent, literals a SyntaxError). The
// two BigInt sites only precomputed constant tables — the Keccak-f[1600] round constants
// and the NTT zeta table — and both are now built with exact int32/double arithmetic
// (Q = 8380417 < 2^23, so every product stays under 2^46 < 2^53).
//
// A wrong entry in either table would NOT crash. It would silently produce wrong keys
// and unspendable funds. So the tables are pinned by these vectors, generated from the
// chain's own implementation (pq.py.algs.ml_dsa_65) — the exact verifier that must
// accept anything this wallet signs. If a table drifts, these fail.
//
// Regenerate with: python -c "from pq.py.algs.ml_dsa_65 import keypair; ..." (see the
// session notes) — never by copying what the code currently outputs.

import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/services/ml_dsa_65.dart';

class _G {
  final String seedHex, pkSha256, skSha256, pkHead, skHead;
  const _G(this.seedHex, this.pkSha256, this.skSha256, this.pkHead, this.skHead);
}

const _golden = <_G>[
    _G('030a11181f262d343b424950575e656c737a81888f969da4abb2b9c0c7ced5dc', 'f03a276f0d38544fe656170c0098c2507ac0a4936f1f0bbf6a3e73cba5dcc42d', 'f0e9f3eb8b4b62a4872668ccb25f92ec15dd7b4158bf42b820574cdc20e973dc', 'f3d7c19f2fe203b1', 'f3d7c19f2fe203b1'),
    _G('222930373e454c535a61686f767d848b9299a0a7aeb5bcc3cad1d8dfe6edf4fb', '07647c1bfbb4196cc17b9feef806a2841d834ab9ffa46a23b5dce78660f382a2', 'bbce7dc3e913b78b6235bb15c4de229031351c42653ecfe879ebaaa216aabc6f', '918e563228f43cb6', '918e563228f43cb6'),
    _G('41484f565d646b727980878e959ca3aab1b8bfc6cdd4dbe2e9f0f7fe050c131a', 'eaef0963ad1d69d575176ecdc001a10014499c7683262062d43db1a952c61432', '4d69ae0bb165505a05c369e2467df10ed71bb9086d88100ecb6a01bc2e8eb3ac', '412b9298b61b8d73', '412b9298b61b8d73'),
];

Uint8List _hex(String h) => Uint8List.fromList(
    List<int>.generate(h.length ~/ 2, (i) => int.parse(h.substring(i * 2, i * 2 + 2), radix: 16)));

String _sha256(Uint8List b) => sha256.convert(b).toString();
String _head(Uint8List b, int n) =>
    b.take(n).map((x) => x.toRadixString(16).padLeft(2, '0')).join();

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('keygen matches the chain implementation byte-for-byte', () async {
    for (final g in _golden) {
      final kp = await MlDsa65.keygen(_hex(g.seedHex));
      expect(kp.publicKey.length, MlDsa65.publicKeyLen);
      expect(kp.secretKey.length, MlDsa65.secretKeyLen);
      expect(_head(kp.publicKey, 8), g.pkHead, reason: 'pk head for seed ${g.seedHex}');
      expect(_head(kp.secretKey, 8), g.skHead, reason: 'sk head for seed ${g.seedHex}');
      expect(_sha256(kp.publicKey), g.pkSha256, reason: 'pk for seed ${g.seedHex}');
      expect(_sha256(kp.secretKey), g.skSha256, reason: 'sk for seed ${g.seedHex}');
    }
  });

  test('sign produces a verifiable signature without crypto.getRandomValues', () async {
    // The engine has no getRandomValues; sign() must supply extraEntropy itself.
    final kp = await MlDsa65.keygen(_hex(_golden.first.seedHex));
    final msg = Uint8List.fromList(utf8.encode('animica known-answer test'));
    final sig = await MlDsa65.sign(kp.secretKey, msg);
    expect(sig.length, MlDsa65.signatureLen);
    expect(await MlDsa65.verify(kp.publicKey, msg, sig), isTrue);

    final tampered = Uint8List.fromList([...msg, 0x21]);
    expect(await MlDsa65.verify(kp.publicKey, tampered, sig), isFalse);
  });

  test('hedged signing gives a different signature each call, same validity', () async {
    // extraEntropy is fresh per call, so two signatures over the same message differ
    // while both verify. If they were ever equal the entropy would not be reaching the
    // bundle.
    final kp = await MlDsa65.keygen(_hex(_golden.last.seedHex));
    final msg = Uint8List.fromList(utf8.encode('hedging check'));
    final a = await MlDsa65.sign(kp.secretKey, msg);
    final b = await MlDsa65.sign(kp.secretKey, msg);
    expect(a, isNot(equals(b)), reason: 'entropy is not reaching the signer');
    expect(await MlDsa65.verify(kp.publicKey, msg, a), isTrue);
    expect(await MlDsa65.verify(kp.publicKey, msg, b), isTrue);
  });
}
