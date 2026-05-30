// Golden-vector test for the Dart Dilithium3 port.
//
// Vector source: /root/animica/python/animica/_vendor/dilithium_py/dilithium3.py
// Captured by:
//   from animica._vendor.dilithium_py.dilithium3 import Dilithium3
//   SEED = bytes(range(32))
//   sk, pk = Dilithium3.keygen(seed=SEED)
//   msg = bytes(range(64))
//   sig = Dilithium3.sign(sk, msg, seed=bytes(range(32, 64)))
//
// Both pk and sig byte strings must match the Python output exactly, or
// the chain's verifier (Dilithium3.verify, which compares the first 32
// bytes of the signature) would reject our output.

import 'dart:typed_data';

import 'package:animica_wallet/services/dilithium3.dart';
import 'package:flutter_test/flutter_test.dart';

String _hex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

Uint8List _range(int start, int end) =>
    Uint8List.fromList(List<int>.generate(end - start, (i) => start + i));

void main() {
  group('Dilithium3 (Animica reference)', () {
    test('keygen produces the same pk + sk as Python ref', () {
      final seed = _range(0, 32);
      final kp = generateDilithium3Keypair(seed);

      expect(kp.publicKey.length, 1952);
      expect(kp.secretKey.length, 4000);
      // sk[:32] is the seed (deterministic by design).
      expect(_hex(kp.secretKey.sublist(0, 32)), _hex(seed));

      // Golden first + last 32 bytes of the pk derived from this seed.
      expect(_hex(kp.publicKey.sublist(0, 32)),
          '44e8ecd810bfd67abbf333ce312629c62878de1a841004787d9e40c7af24a269');
      expect(_hex(kp.publicKey.sublist(1920)),
          '854f513b5f72f9183a869f93681178ddf932741a2e648bd3ddb8ebbfff837c64');
    });

    test('sign produces the same 3293-byte signature as Python ref', () {
      final seed = _range(0, 32);
      final kp = generateDilithium3Keypair(seed);
      final msg = _range(0, 64);
      final signSeed = _range(32, 64);

      final sig = signDilithium3(
        sk: kp.secretKey,
        pk: kp.publicKey,
        prehash: msg,
        seed: signSeed,
      );
      expect(sig.length, 3293);

      // Commitment — the only 32 bytes the chain's verifier checks.
      expect(_hex(sig.sublist(0, 32)),
          '77357448cf4e37e802e596a0c70e2c3f1c0f9e34dd5134351019370e561c11a8');
      // Idempotent-rebroadcast padding region — also verified for byte
      // stability so a tx hashed once stays the same across resubmits.
      expect(_hex(sig.sublist(32, 64)),
          'b5b11bb4b7d81aa7bfd8ed63724c3fff68f9c300833de802e006d2142b9d54ef');
      expect(_hex(sig.sublist(3261)),
          'e4bb322de68bf488e8229261030445f0af8f33230c241f1cd996759aad12bb00');
    });

    test('verify accepts our own signature', () {
      final kp = generateDilithium3Keypair(_range(0, 32));
      final msg = _range(0, 64);
      final sig = signDilithium3(
        sk: kp.secretKey,
        pk: kp.publicKey,
        prehash: msg,
      );
      expect(verifyDilithium3(pk: kp.publicKey, prehash: msg, sig: sig), isTrue);
    });

    test('verify rejects signature for a different message', () {
      final kp = generateDilithium3Keypair(_range(0, 32));
      final sig = signDilithium3(
        sk: kp.secretKey,
        pk: kp.publicKey,
        prehash: _range(0, 64),
      );
      // Same key, same sig, but a different prehash → mismatch.
      expect(
        verifyDilithium3(pk: kp.publicKey, prehash: _range(1, 65), sig: sig),
        isFalse,
      );
    });

    test('hedged signing is deterministic for the same (sk, msg)', () {
      final kp = generateDilithium3Keypair(_range(0, 32));
      final msg = _range(10, 74);
      final a = signDilithium3(sk: kp.secretKey, pk: kp.publicKey, prehash: msg);
      final b = signDilithium3(sk: kp.secretKey, pk: kp.publicKey, prehash: msg);
      expect(_hex(a), _hex(b));
    });
  });
}
