// Golden vectors for the animica:abi:v1 codec, generated from the chain's own
// reference encoder (sdk/python/omni_sdk/types/abi.py). If any of these change,
// the wallet's calldata no longer matches what the node decodes.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/services/abi_codec.dart';

// The token standard's function ABI (subset used on the wire).
final _tokenAbi = parseAbiFunctions({
  'functions': [
    {
      'name': 'init',
      'inputs': [
        {'name': 'name', 'type': 'bytes'},
        {'name': 'symbol', 'type': 'bytes'},
        {'name': 'decimals', 'type': 'int'},
        {'name': 'owner', 'type': 'bytes'},
        {'name': 'initial_supply', 'type': 'int'},
        {'name': 'max_supply', 'type': 'int'},
        {'name': 'mintable', 'type': 'bool'},
        {'name': 'metadata_uri', 'type': 'bytes'},
        {'name': 'freeze_authority', 'type': 'bytes'},
      ],
      'outputs': [],
    },
    {
      'name': 'transfer',
      'inputs': [
        {'name': 'to', 'type': 'bytes'},
        {'name': 'amount', 'type': 'int'},
      ],
      'outputs': [{'type': 'bool'}],
    },
    {
      'name': 'approve',
      'inputs': [
        {'name': 'spender', 'type': 'bytes'},
        {'name': 'amount', 'type': 'int'},
      ],
      'outputs': [{'type': 'bool'}],
    },
    {
      'name': 'balance_of',
      'inputs': [{'name': 'account', 'type': 'bytes'}],
      'outputs': [{'type': 'int'}],
    },
    {'name': 'symbol', 'inputs': [], 'outputs': [{'type': 'bytes'}]},
    {'name': 'decimals', 'inputs': [], 'outputs': [{'type': 'int'}]},
  ],
});

Uint8List _hex(String s) {
  final out = Uint8List(s.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(s.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return out;
}

String _hx(Uint8List b) => b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

final _owner = _hex('aa77b10deb9ed8a9b5e2c42bc3d0b62e6e517b798374951b909e6b7e813d26f9');

void main() {
  group('function selectors (sha3_256("animica:abi:v1|"+sig)[:8])', () {
    const golden = {
      'init': '65b809a9a82a8188',
      'transfer': '25131a4492bd49c8',
      'approve': '686756bbca80ca25',
      'balance_of': 'c786188f2f97900b',
      'symbol': '99f7b69536568c9a',
      'decimals': '68d43e52bfc99da2',
    };
    golden.forEach((name, sel) {
      test('$name -> $sel', () {
        final fn = _tokenAbi.firstWhere((f) => f.name == name);
        expect(_hx(functionSelector(fn)), sel);
      });
    });

    test('signature uses literal "int", not "int256"', () {
      final fn = _tokenAbi.firstWhere((f) => f.name == 'transfer');
      expect(canonicalFnSignature(fn), 'transfer(bytes,int)');
    });
  });

  group('encodeCall', () {
    test('transfer(addr, 123456789)', () {
      final data = encodeCall(_tokenAbi, 'transfer', [_owner, BigInt.from(123456789)]);
      expect(_hx(data),
          '25131a4492bd49c80220aa77b10deb9ed8a9b5e2c42bc3d0b62e6e517b798374951b909e6b7e813d26f90500075bcd15');
    });

    test('approve(addr, 1<<70) — big integer beyond 64 bits', () {
      final data = encodeCall(_tokenAbi, 'approve', [_owner, BigInt.one << 70]);
      expect(_hx(data),
          '686756bbca80ca250220aa77b10deb9ed8a9b5e2c42bc3d0b62e6e517b798374951b909e6b7e813d26f90a00400000000000000000');
    });

    test('balance_of(addr)', () {
      final data = encodeCall(_tokenAbi, 'balance_of', [_owner]);
      expect(_hx(data),
          'c786188f2f97900b0120aa77b10deb9ed8a9b5e2c42bc3d0b62e6e517b798374951b909e6b7e813d26f9');
    });

    test('init(...) full argument spread', () {
      final data = encodeCall(_tokenAbi, 'init', [
        Uint8List.fromList(utf8.encode('MyToken')),
        Uint8List.fromList(utf8.encode('MTK')),
        9,
        _owner,
        BigInt.from(1000000) * BigInt.from(1000000000),
        0,
        true,
        Uint8List.fromList(utf8.encode('ipfs://cid')),
        Uint8List(0),
      ]);
      expect(_hx(data),
          '65b809a9a82a818809074d79546f6b656e034d544b02000920aa77b10deb9ed8a9b5e2c42bc3d0b62e6e517b798374951b909e6b7e813d26f90800038d7ea4c68000020000010a697066733a2f2f63696400');
    });

    test('symbol() — no args', () {
      final data = encodeCall(_tokenAbi, 'symbol', []);
      expect(_hx(data), '99f7b69536568c9a00');
    });

    test('wrong arg count throws', () {
      expect(() => encodeCall(_tokenAbi, 'transfer', [_owner]), throwsA(isA<AbiError>()));
    });
  });

  group('decodeReturn', () {
    test('balance_of -> 5000000000 (BigInt)', () {
      final v = decodeReturn(_tokenAbi, 'balance_of', _hex('010600012a05f200'));
      expect(v, BigInt.from(5000000000));
    });

    test('symbol -> bytes "MTK"', () {
      final v = decodeReturn(_tokenAbi, 'symbol', _hex('01034d544b'));
      expect(v, isA<Uint8List>());
      expect(utf8.decode(v as Uint8List), 'MTK');
    });

    test('decimals -> 9', () {
      final v = decodeReturn(_tokenAbi, 'decimals', _hex('01020009'));
      expect(v, BigInt.from(9));
    });
  });

  group('multidimensional arrays match omni_sdk dim order', () {
    // int[2][3] => 3 outer × 2 inner (the LAST suffix is the outermost length).
    final gridAbi = parseAbiFunctions({
      'functions': [
        {'name': 'grid', 'inputs': [{'name': 'g', 'type': 'int[2][3]'}], 'outputs': []},
      ],
    });

    test('encode grid(int[2][3]) with 3×2 value', () {
      final data = encodeCall(gridAbi, 'grid', [
        [[1, 2], [3, 4], [5, 6]],
      ]);
      expect(_hx(data),
          '55060babd5ba3af70103020200010200020202000302000402020005020006');
    });

    test('a 2×3 value (wrong dim order) is rejected', () {
      expect(
        () => encodeCall(gridAbi, 'grid', [
          [[1, 2, 3], [4, 5, 6]],
        ]),
        throwsA(isA<AbiError>()),
      );
    });
  });

  group('uvarint LEB128', () {
    test('encode boundaries', () {
      expect(_hx(uvarintEncode(0)), '00');
      expect(_hx(uvarintEncode(127)), '7f');
      expect(_hx(uvarintEncode(128)), '8001');
      expect(_hx(uvarintEncode(300)), 'ac02');
    });
    test('round-trips', () {
      for (final n in [0, 1, 127, 128, 255, 16384, 1 << 20]) {
        final enc = uvarintEncode(n);
        final (dec, consumed) = uvarintDecode(enc, 0);
        expect(dec, n);
        expect(consumed, enc.length);
      }
    });

    test('an over-long varint is rejected, not wrapped to a negative length', () {
      // 10 continuation bytes (would be ~2^63 and wrap negative on a 64-bit int).
      final hostile = Uint8List.fromList(
          [0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x01]);
      expect(() => uvarintDecode(hostile, 0), throwsA(isA<AbiError>()));
    });
  });
}
