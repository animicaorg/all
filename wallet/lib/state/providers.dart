/*
 * Animica Wallet — Global providers & easy overrides (Riverpod)
 *
 * This file declares app-wide Riverpod providers for core clients/services
 * and a small helper to create a ProviderContainer with overrides for
 * tests/CLI tooling. In Flutter apps, you typically use:
 *
 *   runApp(ProviderScope(overrides: MyOverrides(...).toOverrides(), child: App()))
 *
 * For non-Flutter contexts (scripts/tests), use:
 *
 *   final container = createContainer(overrides: MyOverrides(rpc: fakeRpc));
 *   final tx = container.read(txServiceProvider);
 *
 * NOTE: We intentionally import `package:riverpod/riverpod.dart` here so this
 * file can be used from pure Dart tests as well as Flutter.
 */

import 'package:riverpod/riverpod.dart';
import 'package:flutter/foundation.dart';

import '../services/rpc_client.dart';
import '../services/ws_client.dart';
import '../services/state_service.dart';
import '../services/tx_service.dart';
import '../services/da_client.dart';
import '../services/aicf_client.dart';
import '../services/randomness_client.dart';
import '../services/light_client.dart';
import '../services/pricing_engine.dart' as pricing;
import '../services/market_data_service.dart';
import '../services/payment_gateway.dart';
import '../services/rpc_marketplace.dart' as marketplace;

/// ============ Base clients ============

/// JSON-RPC HTTP client (auto-closes on provider dispose).
final rpcClientProvider = Provider<RpcClient>((ref) {
  final c = RpcClient.fromEnv();
  ref.onDispose(c.close);
  return c;
});

/// WebSocket client (auto-reconnect). Auto-closes on provider dispose.
final wsClientProvider = Provider<WsClient>((ref) {
  final c = WsClient.fromEnv();
  ref.onDispose(() {
    c.close();
  });
  return c;
});

/// ============ High-level services ============

final stateServiceProvider = Provider<StateService>((ref) {
  final rpc = ref.watch(rpcClientProvider);
  return StateService(rpc);
});

final txServiceProvider = Provider<TxService>((ref) {
  final rpc = ref.watch(rpcClientProvider);
  return TxService(rpc);
});

final daClientProvider = Provider<DaClient>((ref) {
  final c = DaClient.fromEnv();
  ref.onDispose(c.close);
  return c;
});

final aicfClientProvider = Provider<AicfClient>((ref) {
  final c = AicfClient.fromEnv();
  ref.onDispose(c.close);
  return c;
});

final randomnessClientProvider = Provider<RandomnessClient>((ref) {
  final rpc = ref.watch(rpcClientProvider);
  return RandomnessClient(rpc);
});

final lightClientProvider = Provider<LightClient>((ref) {
  final c = LightClient.fromEnv();
  ref.onDispose(c.close);
  return c;
});

/// Marketplace RPC client (for treasury/pricing data)
final marketplaceRpcProvider = Provider<marketplace.RpcClient>((ref) {
  final baseUrl = const String.fromEnvironment(
    'MARKETPLACE_RPC_URL',
    defaultValue: 'http://127.0.0.1:8545',
  );
  return marketplace.RpcClient(baseUrl: baseUrl);
});

/// ============ App state bits (examples) ============
/// Chain ID and RPC URL may also be stored in your dedicated state files.
/// We expose simple notifiers here for convenience when wiring settings UI.

/// Currently-selected chain id (1=test main placeholder, 2=testnet, 1337=local)
final chainIdProvider = StateProvider<int>((ref) => 2);

/// Active RPC HTTP endpoint (string). Your settings page can override this.
final rpcUrlProvider = StateProvider<String>((ref) => const String.fromEnvironment(
      'RPC_HTTP',
      defaultValue: 'http://127.0.0.1:8545',
    ));

/// Active WS endpoint (string). Your settings page can override this.
final wsUrlProvider = StateProvider<String>((ref) => const String.fromEnvironment(
      'RPC_WS',
      defaultValue: 'ws://127.0.0.1:8546',
    ));

/// ============ Overrides helper ============

/// Bag of optional instances you can pass to tests / ProviderScope to replace
/// real network clients with fakes/mocks.
class MyOverrides {
  final RpcClient? rpc;
  final WsClient? ws;
  final StateService? state;
  final TxService? tx;
  final DaClient? da;
  final AicfClient? aicf;
  final RandomnessClient? randomness;
  final LightClient? light;
  final int? chainId;
  final String? rpcUrl;
  final String? wsUrl;

  const MyOverrides({
    this.rpc,
    this.ws,
    this.state,
    this.tx,
    this.da,
    this.aicf,
    this.randomness,
    this.light,
    this.chainId,
    this.rpcUrl,
    this.wsUrl,
  });

  List<Override> toOverrides() => [
        if (rpc != null) rpcClientProvider.overrideWithValue(rpc!),
        if (ws != null) wsClientProvider.overrideWithValue(ws!),
        if (state != null) stateServiceProvider.overrideWithValue(state!),
        if (tx != null) txServiceProvider.overrideWithValue(tx!),
        if (da != null) daClientProvider.overrideWithValue(da!),
        if (aicf != null) aicfClientProvider.overrideWithValue(aicf!),
        if (randomness != null)
          randomnessClientProvider.overrideWithValue(randomness!),
        if (light != null) lightClientProvider.overrideWithValue(light!),
        if (chainId != null)
          chainIdProvider.overrideWith((ref) => chainId!),
        if (rpcUrl != null)
          rpcUrlProvider.overrideWith((ref) => rpcUrl!),
        if (wsUrl != null)
          wsUrlProvider.overrideWith((ref) => wsUrl!),
      ];
}

/// Create a standalone ProviderContainer (useful for tests/CLI).
ProviderContainer createContainer({MyOverrides? overrides}) {
  return ProviderContainer(overrides: overrides?.toOverrides() ?? const []);
}

/// Optional global container for scripts (avoid in Flutter UI; use ProviderScope).
ProviderContainer? _globalContainer;

/// Replace the global container with one using the given overrides.
/// Disposes any previous container to avoid leaks.
ProviderContainer useGlobalContainer({MyOverrides? overrides}) {
  _globalContainer?.dispose();
  _globalContainer = createContainer(overrides: overrides);
  return _globalContainer!;
}

/// Dispose the optional global container (if created).
void disposeGlobalContainer() {
  _globalContainer?.dispose();
  _globalContainer = null;
}

/// Market data service configuration
final marketDataConfigProvider = Provider(
  (_) => MarketDataConfig(
    coinGeckoApiUrl: 'https://api.coingecko.com/api/v3',
    cacheTtl: const Duration(minutes: 5),
    enableWebSocket: true,
  ),
);

/// Payment processor configuration
final paymentProcessorProvider = Provider((ref) {
  final processor = PaymentProcessor();

  // Register available gateways
  processor.registerGateway(
    StripeGateway(
      publishableKey: const String.fromEnvironment('STRIPE_PUBLISHABLE_KEY',
          defaultValue: 'pk_test_...'),
      secretKey: const String.fromEnvironment('STRIPE_SECRET_KEY',
          defaultValue: 'sk_test_...'),
      webhookSecret: const String.fromEnvironment('STRIPE_WEBHOOK_SECRET',
          defaultValue: ''),
    ),
  );

  processor.registerGateway(
    PayPalGateway(
      clientId: const String.fromEnvironment('PAYPAL_CLIENT_ID',
          defaultValue: ''),
      clientSecret: const String.fromEnvironment('PAYPAL_CLIENT_SECRET',
          defaultValue: ''),
      webhookId: const String.fromEnvironment('PAYPAL_WEBHOOK_ID',
          defaultValue: ''),
      sandbox: const bool.hasEnvironment('PAYPAL_SANDBOX'),
    ),
  );

  return processor;
});

/// Market data service instance
final marketDataServiceProvider = Provider((ref) {
  final config = ref.watch(marketDataConfigProvider);
  final service = MarketDataService(config: config);

  // Start live updates in background
  service.startLiveUpdates();

  ref.onDispose(() {
    service.dispose();
  });

  return service;
});

/// Current market price
final currentMarketPriceProvider =
    FutureProvider<pricing.MarketPriceData>((ref) async {
  final rpcClient = ref.watch(marketplaceRpcProvider);

  try {
    // Try to fetch from RPC marketplace methods
    final result = await rpcClient.call('explorer_getMarketData', {'token': 'ANM'});
    return pricing.MarketPriceData(
      price: (result['price'] as num).toDouble(),
      change24h: (result['change24h'] as num?)?.toDouble() ?? 0,
      marketCap: (result['marketCap'] as num?)?.toDouble() ?? 0,
      volume24h: (result['volume24h'] as num?)?.toDouble() ?? 0,
      priceHistory: const [],
      timestamp: DateTime.parse(result['lastUpdate'] as String),
      source: result['source'] as String? ?? 'rpc',
    );
  } catch (e) {
    // Fallback to market data service
    final service = ref.watch(marketDataServiceProvider);
    return service.fetchPrice();
  }
});

/// Price history (30 days)
final priceHistoryProvider = FutureProvider<List<double>>((ref) async {
  final rpcClient = ref.watch(marketplaceRpcProvider);
  
  try {
    // Try to fetch from RPC marketplace methods
    final result = await rpcClient.call('explorer_getPriceHistory', {
      'token': 'ANM',
      'days': 30,
    });
    
    if (result is Map && result.containsKey('prices')) {
      return List<double>.from(
        (result['prices'] as List).map((p) => (p as num).toDouble())
      );
    }
  } catch (e) {
    // Fallback to market data service
  }
  
  final service = ref.watch(marketDataServiceProvider);
  return service.fetchPriceHistory(days: const Duration(days: 30));
});

/// Stream of real-time price updates
final priceUpdatesStreamProvider = StreamProvider<pricing.MarketPriceData>((ref) {
  final service = ref.watch(marketDataServiceProvider);
  return service.priceUpdates;
});

/// Treasury snapshot (from blockchain/API)
final treasurySnapshotProvider =
    FutureProvider<pricing.TreasurySnapshot>((ref) async {
  final rpcClient = ref.watch(marketplaceRpcProvider);

  try {
    // Fetch treasury state from RPC marketplace methods
    final result = await rpcClient.call('explorer_getTreasurySnapshot', {});
    return pricing.TreasurySnapshot(
      totalSupply: (result['totalSupply'] as num).toDouble(),
      soldToDate: (result['soldToDate'] as num).toDouble(),
      treasuryBalance: (result['treasuryBalance'] as num).toDouble(),
      timestamp: DateTime.parse(result['timestamp'] as String),
    );
  } catch (e) {
    debugPrint('Treasury snapshot RPC error: $e');
    // Fallback to sensible defaults
    return pricing.TreasurySnapshot(
      totalSupply: 1e10,
      soldToDate: 2.5e9,
      treasuryBalance: 7.5e9,
      timestamp: DateTime.now(),
    );
  }
});

/// Pricing engine
final pricingEngineProvider =
    FutureProvider<pricing.PricingEngine>((ref) async {
  final marketPrice = await ref.watch(currentMarketPriceProvider.future);
  final treasury = await ref.watch(treasurySnapshotProvider.future);

  return pricing.PricingEngine(
    initialTreasury: treasury,
    initialMarketPrice: marketPrice,
  );
});

/// Current ANM price in USD
final anmPriceProvider = FutureProvider<double>((ref) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.getCurrentPrice();
});

/// Pricing at specific percent sold
final priceAtPercentSoldProvider =
    FutureProvider.family<double, double>((ref, percentSold) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.getPriceAtPercentSold(percentSold);
});

/// Current treasury revenue
final treasuryRevenueProvider = FutureProvider<double>((ref) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.currentRevenue;
});

/// Price to reach $1B target
final priceToReachTargetProvider = FutureProvider<double>((ref) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.priceToReachTarget;
});

/// Years to $1B target
final yearsToTargetProvider = FutureProvider<double>((ref) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.yearsToTargetAtCurrentPrice;
});

/// End-of-year pricing simulation
final eoySimulationProvider =
    FutureProvider<pricing.PricingSimulation>((ref) async {
  final engine = await ref.watch(pricingEngineProvider.future);
  return engine.simulateEndOfYear();
});

/// Purchase flow state notifier
class PurchaseState {
  final double anmQuantity;
  final PaymentMethod? selectedMethod;
  final PaymentIntent? intent;
  final bool isProcessing;
  final String? errorMessage;

  PurchaseState({
    this.anmQuantity = 0,
    this.selectedMethod,
    this.intent,
    this.isProcessing = false,
    this.errorMessage,
  });

  PurchaseState copyWith({
    double? anmQuantity,
    PaymentMethod? selectedMethod,
    PaymentIntent? intent,
    bool? isProcessing,
    String? errorMessage,
  }) {
    return PurchaseState(
      anmQuantity: anmQuantity ?? this.anmQuantity,
      selectedMethod: selectedMethod ?? this.selectedMethod,
      intent: intent ?? this.intent,
      isProcessing: isProcessing ?? this.isProcessing,
      errorMessage: errorMessage ?? this.errorMessage,
    );
  }
}

class PurchaseStateNotifier extends StateNotifier<PurchaseState> {
  final Ref ref;

  PurchaseStateNotifier(this.ref) : super(PurchaseState());

  void setQuantity(double quantity) {
    state = state.copyWith(anmQuantity: quantity);
  }

  void selectPaymentMethod(PaymentMethod method) {
    state = state.copyWith(selectedMethod: method);
  }

  Future<void> createPaymentIntent() async {
    if (state.selectedMethod == null || state.anmQuantity <= 0) {
      state = state.copyWith(
        errorMessage: 'Select method and quantity',
      );
      return;
    }

    state = state.copyWith(isProcessing: true, errorMessage: null);

    try {
      final price = await ref.watch(anmPriceProvider.future);
      final amountUsd = state.anmQuantity * price;
      final processor = ref.watch(paymentProcessorProvider);

      final intent = await processor.createIntent(
        amountUsd: amountUsd,
        tokenQuantity: state.anmQuantity,
        pricePerToken: price,
        method: state.selectedMethod!,
        metadata: {
          'timestamp': DateTime.now().toIso8601String(),
        },
      );

      state = state.copyWith(
        intent: intent,
        isProcessing: false,
      );
    } catch (e) {
      state = state.copyWith(
        isProcessing: false,
        errorMessage: 'Payment setup failed: $e',
      );
    }
  }

  Future<PaymentConfirmation?> completePurchase(
    Map<String, dynamic> paymentData,
  ) async {
    if (state.intent == null || state.selectedMethod == null) {
      state = state.copyWith(errorMessage: 'No active payment intent');
      return null;
    }

    state = state.copyWith(isProcessing: true, errorMessage: null);

    try {
      final processor = ref.watch(paymentProcessorProvider);
      final gateway = processor.getGateway(state.selectedMethod!);

      if (gateway == null) {
        throw Exception('Gateway not available');
      }

      final confirmation = await gateway.confirmPayment(
        intent: state.intent!,
        paymentData: paymentData,
      );

      state = state.copyWith(
        isProcessing: false,
      );

      return confirmation;
    } catch (e) {
      state = state.copyWith(
        isProcessing: false,
        errorMessage: 'Payment failed: $e',
      );
      return null;
    }
  }

  void reset() {
    state = PurchaseState();
  }
}

/// Purchase flow state
final purchaseStateProvider =
    StateNotifierProvider<PurchaseStateNotifier, PurchaseState>(
  (ref) => PurchaseStateNotifier(ref),
);

/// Historical purchase
class HistoricalPurchase {
  final String id;
  final DateTime timestamp;
  final double anmQuantity;
  final double usdAmount;
  final double pricePerAnm;
  final PaymentMethod method;
  final String status;
  final String? receiptUrl;
  final String? transactionHash;

  HistoricalPurchase({
    required this.id,
    required this.timestamp,
    required this.anmQuantity,
    required this.usdAmount,
    required this.pricePerAnm,
    required this.method,
    required this.status,
    this.receiptUrl,
    this.transactionHash,
  });
}

/// Fetch purchase history
final purchaseHistoryProvider =
    FutureProvider<List<HistoricalPurchase>>((ref) async {
  final rpcClient = ref.watch(marketplaceRpcProvider);
  // TODO: Get user address from wallet state
  const userAddress = '0x0000000000000000000000000000000000000000';

  try {
    final result = await rpcClient.call('wallet_getPurchaseHistory', {
      'address': userAddress,
      'limit': 100,
      'offset': 0,
    });
    
    if (result is! Map || !result.containsKey('purchases')) {
      return [];
    }

    return (result['purchases'] as List).map((p) {
      return HistoricalPurchase(
        id: p['id'] ?? '',
        timestamp: DateTime.parse(p['timestamp'] ?? DateTime.now().toString()),
        anmQuantity: (p['anmQuantity'] as num).toDouble(),
        usdAmount: (p['usdAmount'] as num).toDouble(),
        pricePerAnm: (p['pricePerAnm'] as num).toDouble(),
        method: PaymentMethod.creditCard, // TODO: parse from result
        status: p['status'] ?? 'pending',
        receiptUrl: p['receiptUrl'],
        transactionHash: p['transactionHash'],
      );
    }).toList();
  } catch (e) {
    debugPrint('Purchase history fetch error: $e');
    return [];
  }
});

/// Total ANM balance from purchases
final anmBalanceProvider = FutureProvider<double>((ref) async {
  final history = await ref.watch(purchaseHistoryProvider.future);
  return history.fold<double>(
    0,
    (sum, p) => p.status == 'completed' ? sum + p.anmQuantity : sum,
  );
});

/// Total USD spent
final totalSpentProvider = FutureProvider<double>((ref) async {
  final history = await ref.watch(purchaseHistoryProvider.future);
  return history.fold<double>(
    0,
    (sum, p) => p.status == 'completed' ? sum + p.usdAmount : sum,
  );
});

/// Average purchase price
final averagePurchasePriceProvider = FutureProvider<double>((ref) async {
  final balance = await ref.watch(anmBalanceProvider.future);
  final spent = await ref.watch(totalSpentProvider.future);
  return balance > 0 ? spent / balance : 0;
});

/// Dashboard summary
class DashboardSummary {
  final double anmPrice;
  final double anmBalance;
  final double portfolioValue;
  final double treasuryRevenue;
  final double percentToTarget;
  final double yearsToTarget;
  final double priceChange24h;

  DashboardSummary({
    required this.anmPrice,
    required this.anmBalance,
    required this.portfolioValue,
    required this.treasuryRevenue,
    required this.percentToTarget,
    required this.yearsToTarget,
    required this.priceChange24h,
  });
}

/// Comprehensive dashboard summary
final dashboardSummaryProvider = FutureProvider<DashboardSummary>((ref) async {
  final price = await ref.watch(anmPriceProvider.future);
  final balance = await ref.watch(anmBalanceProvider.future);
  final revenue = await ref.watch(treasuryRevenueProvider.future);
  final yearsToTarget = await ref.watch(yearsToTargetProvider.future);
  final marketPrice = await ref.watch(currentMarketPriceProvider.future);

  const targetRevenue = 1e9;
  final percentToTarget = (revenue / targetRevenue * 100).clamp(0, 100).toDouble();

  return DashboardSummary(
    anmPrice: price,
    anmBalance: balance,
    portfolioValue: balance * price,
    treasuryRevenue: revenue,
    percentToTarget: percentToTarget,
    yearsToTarget: yearsToTarget,
    priceChange24h: marketPrice.change24h,
  );
});
