// DEX service logic: constant-product math (mirrors animica_dex_pair),
// slippage guards, promo economics + memo round-trip, and the execution
// capability classifier.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/services/dex.dart';
import 'package:animica_wallet/services/rpc.dart';

BigInt _anm(int n) => BigInt.from(n) * BigInt.from(1000000000);

void main() {
  group('getAmountOut — matches animica_dex_pair._get_amount_out', () {
    test('balanced pool, 30 bps fee', () {
      // reserves 1_000_000 : 1_000_000, feeBps 30 (0.30%), amountIn 1000.
      // out = 1000*9970*1000000 / (1000000*10000 + 1000*9970) = 9960...
      final out = DexService.getAmountOut(
        amountIn: BigInt.from(1000),
        reserveIn: BigInt.from(1000000),
        reserveOut: BigInt.from(1000000),
        feeBps: 30,
      );
      // Recomputed with integer floor division:
      final expected = (BigInt.from(1000) * BigInt.from(9970) * BigInt.from(1000000)) ~/
          (BigInt.from(1000000) * BigInt.from(10000) + BigInt.from(1000) * BigInt.from(9970));
      expect(out, expected);
      expect(out, BigInt.from(996));
    });

    test('zero / empty reserves yield zero', () {
      expect(
          DexService.getAmountOut(
              amountIn: BigInt.from(10), reserveIn: BigInt.zero, reserveOut: _anm(1), feeBps: 30),
          BigInt.zero);
      expect(
          DexService.getAmountOut(
              amountIn: BigInt.zero, reserveIn: _anm(1), reserveOut: _anm(1), feeBps: 30),
          BigInt.zero);
    });

    test('getAmountIn is the inverse-ish (rounds up)', () {
      final rin = _anm(500), rout = _anm(500);
      final needIn = DexService.getAmountIn(
          amountOut: BigInt.from(1000000), reserveIn: rin, reserveOut: rout, feeBps: 30);
      final gotOut = DexService.getAmountOut(
          amountIn: needIn, reserveIn: rin, reserveOut: rout, feeBps: 30);
      // Feeding the required input back yields at least the target output.
      expect(gotOut >= BigInt.from(1000000), isTrue);
    });
  });

  group('minOutWithSlippage', () {
    test('1% slippage off a 1000 quote = 990', () {
      expect(DexService.minOutWithSlippage(BigInt.from(1000), 100), BigInt.from(990));
    });
    test('zero quote stays zero', () {
      expect(DexService.minOutWithSlippage(BigInt.zero, 50), BigInt.zero);
    });
  });

  group('promo economics', () {
    test('total = feeAnmPerDay × days, in nanos', () {
      final one = DexService.promoTotalNanos(1);
      expect(one, _anm(PromoConfig.feeAnmPerDay));
      expect(DexService.promoTotalNanos(3), one * BigInt.from(3));
    });

    test('memo round-trips through parse', () {
      final memo = DexService.promoMemo(
          contract: 'anim1token', days: 7, label: 'My Cool Token');
      final rec = DexService.parsePromoMemo(memo);
      expect(rec, isNotNull);
      expect(rec!.contract, 'anim1token');
      expect(rec.days, 7);
      expect(rec.label, 'My Cool Token');
    });

    test('label pipes are sanitized so parsing stays unambiguous', () {
      final memo = DexService.promoMemo(contract: 'anim1x', days: 1, label: 'a|b|c');
      final rec = DexService.parsePromoMemo(memo);
      expect(rec!.contract, 'anim1x');
      expect(rec.days, 1);
      expect(rec.label, 'a/b/c');
    });

    test('non-promo data is ignored', () {
      expect(DexService.parsePromoMemo(Uint8List.fromList(utf8.encode('ANMSTORE1|x'))), isNull);
      expect(DexService.parsePromoMemo(Uint8List(0)), isNull);
    });
  });

  group('isExecutionDisabled classifier', () {
    test('recognizes disabled-execution reverts', () {
      expect(DexService.isExecutionDisabled(RpcError(-32000, 'contract execution failed')), isTrue);
      expect(DexService.isExecutionDisabled(RpcError(-32000, 'ANIMICA_VM_ALLOW_UNSAFE_EXEC not set')), isTrue);
      expect(DexService.isExecutionDisabled(RpcError(-32000, 'VM_RUNTIME_UNAVAILABLE')), isTrue);
    });
    test('does not flag unrelated errors', () {
      expect(DexService.isExecutionDisabled(RpcError(-32000, 'insufficient balance')), isFalse);
      expect(DexService.isExecutionDisabled(RpcError(-32601, 'method not found')), isFalse);
    });
  });

  group('TokenLaunchParams scaling', () {
    test('initial + max supply scale by 10^decimals', () {
      final p = TokenLaunchParams(
        name: 'Test',
        symbol: 'TST',
        decimals: 6,
        initialSupply: BigInt.from(1000000),
        maxSupply: BigInt.from(2000000),
      );
      expect(p.initialSupplyBase, BigInt.from(1000000) * BigInt.from(10).pow(6));
      expect(p.maxSupplyBase, BigInt.from(2000000) * BigInt.from(10).pow(6));
    });

    test('uncapped max supply stays zero', () {
      final p = TokenLaunchParams(
        name: 'T', symbol: 'T', decimals: 9, initialSupply: BigInt.from(5),
      );
      expect(p.maxSupplyBase, BigInt.zero);
    });
  });
}
