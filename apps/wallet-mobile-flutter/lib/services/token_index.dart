// Off-chain token index client — metadata, images, links, the promoted
// (featured) set, and price history for charts.
//
// Animica has no on-chain log-range query, so token discovery, images/socials
// and price history can't come from the node directly; they come from an
// indexer (the launcher-api registry shape). When no index is configured
// (ANIMICA_TOKEN_INDEX_URL empty) every call returns empty/null and the DEX
// falls back to on-chain-only mode with graceful empty states — nothing here
// ever throws into the UI.
//
// The featured set is reconstructible from chain data alone: promotions are
// ANMPROMO1 transfers to the foundation treasury (see DexService.promoMemo),
// so an indexer can rebuild it without any privileged input.

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../constants.dart';

/// A token as the index knows it. Every market field is nullable: on-chain-only
/// mode leaves them null and the UI shows "—".
class TokenInfo {
  final String address; // anim1… contract address
  final String name;
  final String symbol;
  final int decimals;

  final String? imageUrl;
  final String? description;

  /// e.g. {'website': 'https://…', 'x': 'https://x.com/…', 'telegram': '…'}.
  final Map<String, String> links;

  /// Total supply in base units (÷10^decimals for whole tokens).
  final BigInt? totalSupply;

  /// ANM per 1 whole token (the pool mid-price).
  final double? priceAnm;

  /// Total pool liquidity valued in ANM.
  final double? liquidityAnm;

  /// Signed 24h price change percent.
  final double? change24h;

  /// The ANM/token pair contract, if a pool exists.
  final String? pairAddress;
  final int? feeBps;

  final bool promoted;
  final int? promoDaysLeft;

  const TokenInfo({
    required this.address,
    required this.name,
    required this.symbol,
    required this.decimals,
    this.imageUrl,
    this.description,
    this.links = const {},
    this.totalSupply,
    this.priceAnm,
    this.liquidityAnm,
    this.change24h,
    this.pairAddress,
    this.feeBps,
    this.promoted = false,
    this.promoDaysLeft,
  });

  /// Fully-diluted market cap in ANM (priceAnm × whole-token supply).
  double? get marketCapAnm {
    final p = priceAnm, s = totalSupply;
    if (p == null || s == null) return null;
    return p * (s / BigInt.from(10).pow(decimals)).toDouble();
  }

  static TokenInfo fromJson(Map<String, dynamic> j) => TokenInfo(
        address: (j['address'] ?? j['contract']) as String,
        name: (j['name'] ?? '') as String,
        symbol: (j['symbol'] ?? '') as String,
        decimals: _int(j['decimals']) ?? 9,
        imageUrl: j['imageUrl'] as String? ?? j['image'] as String?,
        description: j['description'] as String?,
        links: _links(j['links']),
        totalSupply: _big(j['totalSupply'] ?? j['total_supply']),
        priceAnm: _dbl(j['priceAnm'] ?? j['price_anm']),
        liquidityAnm: _dbl(j['liquidityAnm'] ?? j['liquidity_anm']),
        change24h: _dbl(j['change24h'] ?? j['change_24h']),
        pairAddress: j['pairAddress'] as String? ?? j['pair'] as String?,
        feeBps: _int(j['feeBps'] ?? j['fee_bps']),
        promoted: (j['promoted'] ?? false) == true,
        promoDaysLeft: _int(j['promoDaysLeft'] ?? j['promo_days_left']),
      );

  static Map<String, String> _links(dynamic v) {
    if (v is Map) {
      return v.map((k, val) => MapEntry(k.toString(), val.toString()))
        ..removeWhere((_, val) => val.isEmpty);
    }
    return const {};
  }

  static int? _int(dynamic v) => v is int ? v : (v is String ? int.tryParse(v) : (v is double ? v.toInt() : null));
  static double? _dbl(dynamic v) => v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);
  static BigInt? _big(dynamic v) {
    if (v is int) return BigInt.from(v);
    if (v is BigInt) return v;
    if (v is String) return BigInt.tryParse(v);
    return null;
  }
}

/// One (heightOrTime, priceAnm) sample for a chart.
class PricePoint {
  final int t; // block height or unix seconds (opaque; used only for ordering)
  final double priceAnm;
  const PricePoint(this.t, this.priceAnm);
}

class TokenIndex {
  final http.Client _client;
  final String _base;
  TokenIndex({http.Client? client, String? baseUrl})
      : _client = client ?? http.Client(),
        _base = (baseUrl ?? AnimicaConfig.tokenIndexUrl).replaceAll(RegExp(r'/$'), '');

  bool get configured => _base.isNotEmpty;

  Future<List<TokenInfo>> featured() => _list('/tokens/featured');

  Future<List<TokenInfo>> listAll() => _list('/tokens');

  /// Search by name, symbol, or contract address. Returns [] with no index.
  Future<List<TokenInfo>> search(String query) {
    final q = query.trim();
    if (q.isEmpty) return featured();
    return _list('/tokens/search?q=${Uri.encodeQueryComponent(q)}');
  }

  Future<TokenInfo?> byAddress(String address) async {
    if (!configured) return null;
    try {
      final r = await _get('/tokens/${Uri.encodeComponent(address)}');
      if (r is Map<String, dynamic>) return TokenInfo.fromJson(r);
    } catch (_) {}
    return null;
  }

  Future<List<PricePoint>> history(String address, {String range = '7d'}) async {
    if (!configured) return const [];
    try {
      final r = await _get('/tokens/${Uri.encodeComponent(address)}/history?range=$range');
      if (r is List) {
        return r
            .whereType<Map>()
            .map((m) => PricePoint(
                  TokenInfo._int(m['t'] ?? m['height'] ?? m['time']) ?? 0,
                  TokenInfo._dbl(m['priceAnm'] ?? m['price_anm'] ?? m['p']) ?? 0,
                ))
            .where((p) => p.priceAnm > 0)
            .toList();
      }
    } catch (_) {}
    return const [];
  }

  Future<List<TokenInfo>> _list(String path) async {
    if (!configured) return const [];
    try {
      final r = await _get(path);
      final items = r is List ? r : (r is Map && r['tokens'] is List ? r['tokens'] as List : const []);
      return items.whereType<Map>().map((m) => TokenInfo.fromJson(Map<String, dynamic>.from(m))).toList();
    } catch (_) {
      return const [];
    }
  }

  Future<dynamic> _get(String path) async {
    final resp = await _client.get(Uri.parse('$_base$path')).timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw http.ClientException('index http ${resp.statusCode}', Uri.parse('$_base$path'));
    }
    return jsonDecode(resp.body);
  }

  void close() => _client.close();
}
