// Riverpod wiring for the token launcher + DEX.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/dex.dart';
import '../services/price.dart';
import '../services/token_index.dart';
import 'wallet_state.dart';

export '../services/dex.dart' show DexCapability, DexService, TokenLaunchParams, PromoConfig;
export '../services/token_index.dart' show TokenInfo, PricePoint;

final dexServiceProvider = Provider<DexService>((ref) {
  return DexService(ref.watch(rpcProvider));
});

final tokenIndexProvider = Provider<TokenIndex>((ref) {
  final idx = TokenIndex();
  ref.onDispose(idx.close);
  return idx;
});

/// Whether the connected node executes contracts (probed once, cached until
/// invalidated). Drives the honest "DEX not live on this network yet" banner.
final dexCapabilityProvider = FutureProvider<DexCapability>((ref) async {
  return ref.watch(dexServiceProvider).probeExecution();
});

/// Promoted / featured tokens for the carousel.
final featuredTokensProvider = FutureProvider.autoDispose<List<TokenInfo>>((ref) async {
  return ref.watch(tokenIndexProvider).featured();
});

/// Search by name, symbol, or contract address.
final tokenSearchProvider =
    FutureProvider.autoDispose.family<List<TokenInfo>, String>((ref, query) async {
  return ref.watch(tokenIndexProvider).search(query);
});

/// Full metadata + market stats for one token.
final tokenInfoProvider =
    FutureProvider.autoDispose.family<TokenInfo?, String>((ref, address) async {
  return ref.watch(tokenIndexProvider).byAddress(address);
});

/// Price history (ANM per token) for the chart.
final tokenHistoryProvider =
    FutureProvider.autoDispose.family<List<PricePoint>, String>((ref, address) async {
  return ref.watch(tokenIndexProvider).history(address);
});

/// A token's market figures rendered in both ANM and USD, using the live ANM
/// price. Every field degrades to null when its inputs are missing, so callers
/// show "—" instead of a wrong number.
class TokenUsdStats {
  final double? anmUsd; // 1 ANM in USD
  final double? priceAnm; // 1 token in ANM
  final double? priceUsd; // 1 token in USD
  final double? marketCapAnm;
  final double? marketCapUsd;
  final double? liquidityAnm;
  final double? liquidityUsd;
  final double? change24h;

  const TokenUsdStats({
    this.anmUsd,
    this.priceAnm,
    this.priceUsd,
    this.marketCapAnm,
    this.marketCapUsd,
    this.liquidityAnm,
    this.liquidityUsd,
    this.change24h,
  });

  factory TokenUsdStats.from(TokenInfo info, AnmMarket? market) {
    final anmUsd = market?.usdPerAnm;
    double? usd(double? anm) => (anm != null && anmUsd != null) ? anm * anmUsd : null;
    return TokenUsdStats(
      anmUsd: anmUsd,
      priceAnm: info.priceAnm,
      priceUsd: usd(info.priceAnm),
      marketCapAnm: info.marketCapAnm,
      marketCapUsd: usd(info.marketCapAnm),
      liquidityAnm: info.liquidityAnm,
      liquidityUsd: usd(info.liquidityAnm),
      change24h: info.change24h,
    );
  }

  /// USD value of holding [whole] tokens.
  double? holdingsUsd(double whole) => priceUsd == null ? null : priceUsd! * whole;

  /// ANM value of holding [whole] tokens.
  double? holdingsAnm(double whole) => priceAnm == null ? null : priceAnm! * whole;
}
