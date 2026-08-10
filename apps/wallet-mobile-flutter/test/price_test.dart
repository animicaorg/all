// NonKYC price service — parsing, fiat math, and formatting.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:animica_wallet/services/price.dart';

// Trimmed real response from
// https://api.nonkyc.io/api/v2/market/getbysymbol/ANM_USDT (2026-08-10).
const _sample = '''
{
  "symbol": "ANM/USDT",
  "primaryTicker": "ANM",
  "lastPrice": "0.00010082",
  "yesterdayPrice": "0.00007993",
  "priceDecimals": 8,
  "isActive": true,
  "changePercent": "+26.13",
  "lastPriceNumber": 0.00010082,
  "changePercentNumber": 26.13
}
''';

void main() {
  group('AnmMarket.fromJson', () {
    test('parses the live NonKYC shape', () {
      final m = AnmMarket.fromJson(jsonDecode(_sample) as Map<String, dynamic>);
      expect(m.usdPerAnm, closeTo(0.00010082, 1e-12));
      expect(m.changePct24h, closeTo(26.13, 1e-9));
    });

    test('falls back to string fields', () {
      final m = AnmMarket.fromJson({
        'lastPrice': '0.00007993',
        'changePercent': '-4.20',
      });
      expect(m.usdPerAnm, closeTo(0.00007993, 1e-12));
      expect(m.changePct24h, closeTo(-4.20, 1e-9));
    });

    test('missing change is null, missing price throws', () {
      final m = AnmMarket.fromJson({'lastPriceNumber': 0.5});
      expect(m.changePct24h, isNull);
      expect(() => AnmMarket.fromJson({'symbol': 'ANM/USDT'}),
          throwsFormatException);
      expect(() => AnmMarket.fromJson({'lastPrice': '0'}),
          throwsFormatException);
    });
  });

  group('PriceService', () {
    test('fetches and parses via http', () async {
      final svc = PriceService(MockClient((req) async {
        expect(req.url.host, 'api.nonkyc.io');
        return http.Response(_sample, 200,
            headers: {'content-type': 'application/json'});
      }));
      final m = await svc.fetchAnmUsd();
      expect(m.usdPerAnm, closeTo(0.00010082, 1e-12));
    });

    test('non-200 throws', () async {
      final svc = PriceService(MockClient((req) async {
        return http.Response('gateway error', 502);
      }));
      expect(svc.fetchAnmUsd(), throwsA(isA<http.ClientException>()));
    });
  });

  group('usdValueOfNanos', () {
    test('5000 ANM at \$0.00010082', () {
      final nanos = BigInt.from(5000) * BigInt.from(1000000000);
      expect(usdValueOfNanos(nanos, 0.00010082), closeTo(0.5041, 1e-9));
    });
    test('zero balance', () {
      expect(usdValueOfNanos(BigInt.zero, 0.1), 0);
    });
  });

  group('formatUsd', () {
    test('zero and negatives', () {
      expect(formatUsd(0), r'$0.00');
      expect(formatUsd(-1), r'$0.00');
      expect(formatUsd(double.nan), r'$0.00');
    });
    test('cents and dollars', () {
      expect(formatUsd(0.42), r'$0.42');
      expect(formatUsd(1234.5), r'$1,234.50');
      expect(formatUsd(1234567.891), r'$1,234,567.89');
    });
    test('sub-cent keeps significant digits', () {
      expect(formatUsd(0.0051), r'$0.0051');
      expect(formatUsd(0.00010082, sigFigs: 3), r'$0.000101');
    });
    test('dust floors instead of rounding to zero', () {
      expect(formatUsd(0.00000001), r'<$0.0000001');
    });
    test('astronomically large values do not crash', () {
      // Dart flips toStringAsFixed to exponent notation near 1e21.
      expect(() => formatUsd(1e21), returnsNormally);
      expect(() => formatUsd(5e24), returnsNormally);
      expect(formatUsd(1e21).startsWith(r'$'), isTrue);
    });
  });
}
