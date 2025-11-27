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

import '../services/rpc_client.dart';
import '../services/ws_client.dart';
import '../services/state_service.dart';
import '../services/tx_service.dart';
import '../services/da_client.dart';
import '../services/aicf_client.dart';
import '../services/randomness_client.dart';
import '../services/light_client.dart';

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
