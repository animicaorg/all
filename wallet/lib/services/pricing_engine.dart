/// Animica Pricing Engine
/// 
/// Implements dynamic pricing for ANM token sales:
/// - Base price: $1.00 USD
/// - Exchange rate integration (CoinGecko, CoinMarketCap)
/// - Explorer-based price feeds (15% markup)
/// - Treasury-aware pricing with escalation curve to reach $1B
/// - Deterministic pricing model (reproducible across clients)
///
/// Formula:
///   effectivePrice = max(basePrice, exchangePrice * 1.15) * treasuryMultiplier
///   treasuryMultiplier = 1.0 + (percentSold / curve)

import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';

const double _basePrice = 1.00; // USD
const double _exchangeMarkup = 1.15; // 15% markup on market price
const double _treasuryTarget = 1e9; // $1 billion

/// Treasury snapshot for pricing calculations
class TreasurySnapshot {
  final double totalSupply; // Total ANM minted
  final double soldToDate; // ANM sold from treasury
  final double treasuryBalance; // ANM remaining in treasury
  final DateTime timestamp;

  TreasurySnapshot({
    required this.totalSupply,
    required this.soldToDate,
    required this.treasuryBalance,
    required this.timestamp,
  });

  double get percentSold => totalSupply > 0 ? (soldToDate / totalSupply) * 100 : 0;

  /// Estimate revenue if all treasury is sold at given price
  double projectedRevenue(double priceUsd) {
    return treasuryBalance * priceUsd;
  }

  /// Estimate remaining price to reach $1B target
  double priceToReachTarget(double currentRevenue) {
    final remaining = _treasuryTarget - currentRevenue;
    if (treasuryBalance <= 0) return 0;
    return remaining / treasuryBalance;
  }
}

/// Market price data from exchanges
class MarketPriceData {
  final double price; // Current USD price
  final double change24h; // % change in last 24h
  final double marketCap; // Total market cap (USD)
  final double volume24h; // 24h trading volume (USD)
  final List<double> priceHistory; // Last 7 days (daily close)
  final DateTime timestamp;
  final String source; // e.g., "coingecko", "coinmarketcap", "explorer"

  MarketPriceData({
    required this.price,
    required this.change24h,
    required this.marketCap,
    required this.volume24h,
    required this.priceHistory,
    required this.timestamp,
    required this.source,
  });

  /// Return markup price for treasury sales
  double get withMarkup => price * _exchangeMarkup;

  /// SMA over history (for trend)
  double get averagePrice =>
      priceHistory.isEmpty ? price : priceHistory.reduce((a, b) => a + b) / priceHistory.length;

  /// Momentum indicator (-1 to 1)
  double get momentum {
    if (priceHistory.length < 2) return 0;
    final oldest = priceHistory.first;
    final newest = priceHistory.last;
    if (oldest == 0) return 0;
    return ((newest - oldest) / oldest).clamp(-1, 1);
  }
}

/// Annual pricing simulation result
class PricingSimulation {
  final double currentPrice;
  final double endOfYearPrice; // Projected price at end of year
  final double revenueAtEoy; // Projected treasury revenue
  final double remainingSupply; // ANM left if sold at pace
  final bool reachesTarget; // Whether $1B is reached
  final double yearsToTarget; // Years until $1B at current pace

  PricingSimulation({
    required this.currentPrice,
    required this.endOfYearPrice,
    required this.revenueAtEoy,
    required this.remainingSupply,
    required this.reachesTarget,
    required this.yearsToTarget,
  });
}

/// Core pricing engine with deterministic calculations
class PricingEngine {
  late TreasurySnapshot _treasurySnapshot;
  late MarketPriceData _marketPrice;
  late DateTime _lastUpdate;

  PricingEngine({
    required TreasurySnapshot initialTreasury,
    required MarketPriceData initialMarketPrice,
  }) {
    _treasurySnapshot = initialTreasury;
    _marketPrice = initialMarketPrice;
    _lastUpdate = DateTime.now();
  }

  /// Update treasury data
  void updateTreasury(TreasurySnapshot snapshot) {
    _treasurySnapshot = snapshot;
    _lastUpdate = DateTime.now();
  }

  /// Update market price
  void updateMarketPrice(MarketPriceData data) {
    _marketPrice = data;
    _lastUpdate = DateTime.now();
  }

  /// Calculate current effective price
  /// 
  /// Formula:
  ///   exchangePrice = max(basePrice, marketPrice * 1.15)
  ///   treasuryMultiplier = 1.0 + (treasurySoldPercent / 100) * scaleFactor
  ///   effectivePrice = exchangePrice * treasuryMultiplier
  double getCurrentPrice() {
    final exchangePrice = _computeExchangePrice(_marketPrice.price);
    final treasuryMultiplier = _computeTreasuryMultiplier(_treasurySnapshot.percentSold);
    return exchangePrice * treasuryMultiplier;
  }

  /// Estimate price at given amount sold (in %)
  double getPriceAtPercentSold(double percentSold) {
    final exchangePrice = _computeExchangePrice(_marketPrice.price);
    final multiplier = _computeTreasuryMultiplier(percentSold.clamp(0, 100));
    return exchangePrice * multiplier;
  }

  /// Return treasury snapshot
  TreasurySnapshot get treasury => _treasurySnapshot;

  /// Return market price data
  MarketPriceData get marketPrice => _marketPrice;

  /// Revenue generated so far
  double get currentRevenue =>
      _treasurySnapshot.soldToDate * getCurrentPrice();

  /// Revenue if all remaining treasury sold at current price
  double get projectedRevenueAtCurrentPrice =>
      _treasurySnapshot.treasuryBalance * getCurrentPrice() + currentRevenue;

  /// Price needed to reach $1B target
  double get priceToReachTarget {
    final remaining = _treasuryTarget - currentRevenue;
    if (remaining <= 0) return 0; // Already exceeded
    if (_treasurySnapshot.treasuryBalance <= 0) return double.infinity;
    return remaining / _treasurySnapshot.treasuryBalance;
  }

  /// Years to target assuming linear sales
  double get yearsToTargetAtCurrentPrice {
    final currentSaleRate =
        _treasurySnapshot.soldToDate / _treasurySnapshot.totalSupply;
    if (currentSaleRate <= 0 || currentRevenue >= _treasuryTarget) return 0;

    final revenuePerYear =
        (currentRevenue / (DateTime.now().year - 2024)) * 365; // Simple annualization
    if (revenuePerYear <= 0) return double.infinity;

    final yearsRemaining = (_treasuryTarget - currentRevenue) / revenuePerYear;
    return yearsRemaining.clamp(0, double.infinity);
  }

  /// Simulate EOY prices and revenue
  PricingSimulation simulateEndOfYear() {
    final currentPrice = getCurrentPrice();

    // Assume 30% of remaining treasury sells over next year at escalating price
    const double selloffRate = 0.30;
    final projectedSoldInYear =
        _treasurySnapshot.treasuryBalance * selloffRate;
    final projectedPercentAtEoy =
        _treasurySnapshot.percentSold + (projectedSoldInYear / _treasurySnapshot.totalSupply) * 100;

    final eoyPrice = getPriceAtPercentSold(projectedPercentAtEoy.clamp(0, 100));
    final eoyRevenue =
        currentRevenue + (projectedSoldInYear * eoyPrice);
    final remainingSupply =
        _treasurySnapshot.treasuryBalance - projectedSoldInYear;

    return PricingSimulation(
      currentPrice: currentPrice,
      endOfYearPrice: eoyPrice,
      revenueAtEoy: eoyRevenue,
      remainingSupply: remainingSupply.clamp(0, double.infinity),
      reachesTarget: eoyRevenue >= _treasuryTarget,
      yearsToTarget: yearsToTargetAtCurrentPrice,
    );
  }

  /// Hash-based deterministic pricing seed (prevents replay of quotes)
  String generateQuoteHash({
    required int quantity,
    required Duration validFor,
  }) {
    final components = [
      quantity.toString(),
      getCurrentPrice().toStringAsFixed(8),
      _treasurySnapshot.percentSold.toStringAsFixed(4),
      _lastUpdate.millisecondsSinceEpoch.toString(),
      validFor.inSeconds.toString(),
    ].join('|');

    // Simple deterministic hash (use crypto.sha256 in production)
    return _simpleHash(components);
  }

  // ---- Private methods ----

  /// Compute exchange-based price with markup
  static double _computeExchangePrice(double marketPrice) {
    return math.max(_basePrice, marketPrice * _exchangeMarkup);
  }

  /// Compute treasury escalation multiplier
  /// 
  /// Curve: 1.0 → ~3.0 as more is sold
  /// Encourages early adoption; incentivizes purchases as treasury depletes
  static double _computeTreasuryMultiplier(double percentSold) {
    const scaleFactor = 2.0; // Multiplier grows by 2x over full depletion
    // S-curve-ish: slow start, accelerating mid-range
    final ratio = (percentSold / 100).clamp(0, 1);
    // Use quadratic ease-out: 1 + 2*(sqrt(ratio))
    return 1.0 + scaleFactor * math.sqrt(ratio);
  }

  /// Simple deterministic hash for quotes
  static String _simpleHash(String input) {
    var hash = 0;
    for (int i = 0; i < input.length; i++) {
      final char = input.codeUnitAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Keep it 32-bit
    }
    return hash.toUnsigned(32).toRadixString(16).padLeft(8, '0');
  }
}

