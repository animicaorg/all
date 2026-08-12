// Live ANM market data from the NonKYC exchange public API.
//
// GET https://api.nonkyc.io/api/v2/market/getbysymbol/ANM_USDT returns one
// JSON object for the pair. Fields used here:
//
//   lastPriceNumber       last trade price in USDT (number)
//   lastPrice             same as a string — fallback when the number is absent
//   changePercentNumber   signed 24h change percent (number)
//   changePercent         same as a string like "+26.13"
//
// USDT is displayed as $ (≈ USD).

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../constants.dart';

class AnmMarket {
  /// Last trade price of 1 ANM in USDT (≈ USD).
  final double usdPerAnm;

  /// Signed 24-hour change in percent (e.g. 26.13, -4.2). Null when the
  /// exchange omits it.
  final double? changePct24h;

  const AnmMarket({required this.usdPerAnm, this.changePct24h});

  static AnmMarket fromJson(Map<String, dynamic> j) {
    final price = _num(j['lastPriceNumber']) ?? _num(j['lastPrice']);
    if (price == null || price <= 0) {
      throw const FormatException('no usable lastPrice in market response');
    }
    return AnmMarket(
      usdPerAnm: price,
      changePct24h: _num(j['changePercentNumber']) ?? _num(j['changePercent']),
    );
  }

  static double? _num(dynamic v) {
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v.replaceFirst('+', ''));
    return null;
  }
}

class PriceService {
  final http.Client _client;
  PriceService([http.Client? client]) : _client = client ?? http.Client();

  Future<AnmMarket> fetchAnmUsd() async {
    final resp = await _client
        .get(Uri.parse(AnimicaConfig.priceApiUrl))
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) {
      throw http.ClientException(
          'price api http ${resp.statusCode}', Uri.parse(AnimicaConfig.priceApiUrl));
    }
    final body = jsonDecode(resp.body);
    if (body is! Map<String, dynamic>) {
      throw const FormatException('unexpected price api shape');
    }
    return AnmMarket.fromJson(body);
  }

  void close() => _client.close();
}

/// Fiat value of a nano-ANM balance at [usdPerAnm].
double usdValueOfNanos(BigInt nanos, double usdPerAnm) =>
    nanos.toDouble() / 1e9 * usdPerAnm;

/// Format a dollar amount for display. Values ≥ $0.01 get the usual two
/// decimals (with thousands separators); smaller ones keep [sigFigs]
/// significant digits so a small-but-real balance never shows as $0.00.
String formatUsd(double v, {int sigFigs = 2}) {
  if (!v.isFinite || v <= 0) return r'$0.00';
  // Dart's toStringAsFixed flips to exponent notation at ~1e21 (a string with
  // no '.'), which would crash the split below. No real ANM balance reaches
  // that, so just show a compact form instead of dividing by zero on `parts`.
  if (v >= 1e18) return '\$${v.toStringAsExponential(2)}';
  if (v >= 0.01) {
    final s = v.toStringAsFixed(2);
    final parts = s.split('.');
    final whole = parts[0].replaceAllMapped(
        RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ',');
    return '\$$whole.${parts[1]}';
  }
  if (v < 0.0000001) return r'<$0.0000001';
  var s = v.toStringAsPrecision(sigFigs);
  if (s.contains('e')) {
    // Never show exponent notation in the UI.
    s = v.toStringAsFixed(10).replaceAll(RegExp(r'0+$'), '');
  }
  return '\$$s';
}
