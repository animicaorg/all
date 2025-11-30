/// Animica Market Data Service
/// 
/// Integrates with:
/// - CoinGecko API (free, no auth)
/// - CoinMarketCap API (premium data)
/// - Animica Explorer API (on-chain price feeds)
/// 
/// Provides:
/// - Real-time price updates via WebSocket
/// - Price history aggregation
/// - Cache layer with TTL
/// - Fallback sources for resilience

import 'dart:async';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'pricing_engine.dart';

enum PriceSource {
  coingecko,
  coinmarketcap,
  explorer,
}

/// Cache entry with TTL
class _CachedPrice {
  final MarketPriceData data;
  final DateTime cachedAt;
  final Duration ttl;

  _CachedPrice({
    required this.data,
    required this.ttl,
  }) : cachedAt = DateTime.now();

  bool get isExpired => DateTime.now().difference(cachedAt) > ttl;
}

/// Market data service configuration
class MarketDataConfig {
  final String coinGeckoApiUrl;
  final String coinMarketCapApiUrl;
  final String coinMarketCapApiKey;
  final String explorerApiUrl;
  final String explorerApiKey;
  final Duration cacheTtl;
  final bool enableWebSocket;

  const MarketDataConfig({
    this.coinGeckoApiUrl = 'https://api.coingecko.com/api/v3',
    this.coinMarketCapApiUrl = 'https://pro-api.coinmarketcap.com/v2',
    this.coinMarketCapApiKey = '',
    this.explorerApiUrl = 'http://localhost:8545',
    this.explorerApiKey = '',
    this.cacheTtl = const Duration(minutes: 5),
    this.enableWebSocket = true,
  });
}

/// Market data provider
class MarketDataService {
  final MarketDataConfig config;
  final http.Client httpClient;

  _CachedPrice? _cachedPrice;
  StreamSubscription? _wsSubscription;
  final StreamController<MarketPriceData> _priceUpdates =
      StreamController<MarketPriceData>.broadcast();

  MarketDataService({
    required this.config,
    http.Client? httpClient,
  }) : httpClient = httpClient ?? http.Client();

  /// Subscribe to real-time price updates
  Stream<MarketPriceData> get priceUpdates => _priceUpdates.stream;

  /// Fetch current price (with caching)
  Future<MarketPriceData> fetchPrice({
    PriceSource source = PriceSource.coingecko,
    bool forceRefresh = false,
  }) async {
    // Check cache
    if (!forceRefresh && _cachedPrice != null && !_cachedPrice!.isExpired) {
      return _cachedPrice!.data;
    }

    try {
      final data = switch (source) {
        PriceSource.coingecko => await _fetchCoinGeckoPrice(),
        PriceSource.coinmarketcap => await _fetchCoinMarketCapPrice(),
        PriceSource.explorer => await _fetchExplorerPrice(),
      };

      _cachedPrice = _CachedPrice(data: data, ttl: config.cacheTtl);
      _priceUpdates.add(data);
      return data;
    } catch (e) {
      // Try fallback source
      if (source != PriceSource.coingecko) {
        debugPrint('Market data fetch failed ($source), trying CoinGecko');
        return fetchPrice(source: PriceSource.coingecko, forceRefresh: forceRefresh);
      }
      rethrow;
    }
  }

  /// Fetch 30-day price history
  Future<List<double>> fetchPriceHistory({
    Duration? days,
    PriceSource source = PriceSource.coingecko,
  }) async {
    final dayCount = (days?.inDays ?? 30).clamp(1, 365);

    return switch (source) {
      PriceSource.coingecko => await _fetchCoinGeckoHistory(dayCount),
      PriceSource.coinmarketcap => await _fetchCoinMarketCapHistory(dayCount),
      PriceSource.explorer => await _fetchExplorerHistory(dayCount),
    };
  }

  /// Start WebSocket stream for live updates (if enabled)
  Future<void> startLiveUpdates() async {
    if (!config.enableWebSocket) return;

    try {
      // For real implementation, connect to exchange WebSocket
      // This is a placeholder that polls instead
      _wsSubscription = Stream.periodic(const Duration(seconds: 30)).listen((_) {
        fetchPrice(forceRefresh: true);
      });
    } catch (e) {
      debugPrint('Failed to start live updates: $e');
    }
  }

  /// Stop WebSocket stream
  Future<void> stopLiveUpdates() async {
    await _wsSubscription?.cancel();
  }

  /// Dispose service
  void dispose() {
    _wsSubscription?.cancel();
    _priceUpdates.close();
    httpClient.close();
  }

  // ---- Private: CoinGecko ----

  Future<MarketPriceData> _fetchCoinGeckoPrice() async {
    final url = Uri.parse(
      '${config.coinGeckoApiUrl}/simple/price?'
      'ids=animica&vs_currencies=usd&'
      'include_market_cap=true&include_24hr_vol=true&'
      'include_24hr_change=true',
    );

    final res = await httpClient.get(url);
    if (res.statusCode != 200) {
      throw Exception('CoinGecko API error: ${res.statusCode}');
    }

    final json = jsonDecode(res.body) as Map<String, dynamic>;
    final data = json['animica'] as Map<String, dynamic>? ?? {};

    return MarketPriceData(
      price: (data['usd'] as num?)?.toDouble() ?? 1.0,
      change24h: (data['usd_24h_change'] as num?)?.toDouble() ?? 0.0,
      marketCap: (data['usd_market_cap'] as num?)?.toDouble() ?? 0.0,
      volume24h: (data['usd_24h_vol'] as num?)?.toDouble() ?? 0.0,
      priceHistory: [1.0], // CoinGecko free tier doesn't have history
      timestamp: DateTime.now(),
      source: 'coingecko',
    );
  }

  Future<List<double>> _fetchCoinGeckoHistory(int days) async {
    final url = Uri.parse(
      '${config.coinGeckoApiUrl}/coins/animica/market_chart?'
      'vs_currency=usd&days=$days',
    );

    try {
      final res = await httpClient.get(url);
      if (res.statusCode != 200) return [];

      final json = jsonDecode(res.body) as Map<String, dynamic>;
      final prices = json['prices'] as List? ?? [];

      return prices
          .cast<List>()
          .map((p) => (p[1] as num).toDouble())
          .toList();
    } catch (_) {
      return [];
    }
  }

  // ---- Private: CoinMarketCap ----

  Future<MarketPriceData> _fetchCoinMarketCapPrice() async {
    if (config.coinMarketCapApiKey.isEmpty) {
      throw Exception('CoinMarketCap API key not configured');
    }

    final url = Uri.parse(
      '${config.coinMarketCapApiUrl}/cryptocurrency/quotes/latest?'
      'slug=animica&convert=USD',
    );

    final res = await httpClient.get(
      url,
      headers: {'X-CMC_PRO_API_KEY': config.coinMarketCapApiKey},
    );

    if (res.statusCode != 200) {
      throw Exception('CoinMarketCap API error: ${res.statusCode}');
    }

    final json = jsonDecode(res.body) as Map<String, dynamic>;
    final data = ((json['data'] ?? {}) as Map<String, dynamic>)['animica']
        as Map<String, dynamic>?;
    final quote = (data?['quote'] ?? {}) as Map<String, dynamic>?;
    final usd = (quote?['USD'] ?? {}) as Map<String, dynamic>?;

    return MarketPriceData(
      price: (usd?['price'] as num?)?.toDouble() ?? 1.0,
      change24h: (usd?['percent_change_24h'] as num?)?.toDouble() ?? 0.0,
      marketCap: (usd?['market_cap'] as num?)?.toDouble() ?? 0.0,
      volume24h: (usd?['volume_24h'] as num?)?.toDouble() ?? 0.0,
      priceHistory: [],
      timestamp: DateTime.now(),
      source: 'coinmarketcap',
    );
  }

  Future<List<double>> _fetchCoinMarketCapHistory(int days) async {
    // CMC historical data requires paid endpoints; return empty
    return [];
  }

  // ---- Private: Animica Explorer ----

  Future<MarketPriceData> _fetchExplorerPrice() async {
    final url = Uri.parse('${config.explorerApiUrl}/rpc');

    final body = jsonEncode({
      'jsonrpc': '2.0',
      'id': 1,
      'method': 'explorer_getMarketData',
      'params': ['ANM'],
    });

    try {
      final res = await httpClient.post(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (config.explorerApiKey.isNotEmpty) 'X-API-Key': config.explorerApiKey,
        },
        body: body,
      );

      if (res.statusCode != 200) {
        throw Exception('Explorer API error: ${res.statusCode}');
      }

      final json = jsonDecode(res.body) as Map<String, dynamic>;
      final result = json['result'] as Map<String, dynamic>? ?? {};

      return MarketPriceData(
        price: (result['price'] as num?)?.toDouble() ?? 1.0,
        change24h: (result['change_24h'] as num?)?.toDouble() ?? 0.0,
        marketCap: (result['market_cap'] as num?)?.toDouble() ?? 0.0,
        volume24h: (result['volume_24h'] as num?)?.toDouble() ?? 0.0,
        priceHistory: _parseHistory(result['history']),
        timestamp: DateTime.now(),
        source: 'explorer',
      );
    } catch (e) {
      debugPrint('Explorer API error: $e');
      rethrow;
    }
  }

  Future<List<double>> _fetchExplorerHistory(int days) async {
    final url = Uri.parse('${config.explorerApiUrl}/rpc');

    final body = jsonEncode({
      'jsonrpc': '2.0',
      'id': 1,
      'method': 'explorer_getPriceHistory',
      'params': ['ANM', days],
    });

    try {
      final res = await httpClient.post(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (config.explorerApiKey.isNotEmpty) 'X-API-Key': config.explorerApiKey,
        },
        body: body,
      );

      if (res.statusCode == 200) {
        final json = jsonDecode(res.body) as Map<String, dynamic>;
        return _parseHistory(json['result']);
      }
    } catch (_) {}

    return [];
  }

  static List<double> _parseHistory(dynamic data) {
    if (data is! List) return [];
    return data.whereType<num>().map((n) => n.toDouble()).toList();
  }
}
