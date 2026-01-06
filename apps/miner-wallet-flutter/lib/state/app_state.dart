/// Global application state providers
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:logging/logging.dart';

import '../models/miner_config.dart';
import '../services/config_service.dart';
import '../services/rpc_service.dart';

/// Configuration service provider
final configServiceProvider = Provider<ConfigService>((ref) {
  return ConfigService();
});

/// Miner configuration provider
final configProvider = StateNotifierProvider<ConfigNotifier, MinerConfig>((ref) {
  return ConfigNotifier(ref.watch(configServiceProvider));
});

class ConfigNotifier extends StateNotifier<MinerConfig> {
  final ConfigService _configService;
  final _log = Logger('ConfigNotifier');

  ConfigNotifier(this._configService) : super(MinerConfig.defaults()) {
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    try {
      final config = await _configService.load();
      state = config;
    } catch (e, stackTrace) {
      _log.warning('Failed to load config', e, stackTrace);
    }
  }

  Future<void> updateConfig(MinerConfig config) async {
    state = config;
    await _configService.save(config);
  }

  Future<void> updateNetworkConfig(NetworkConfig network) async {
    final config = state.copyWith(network: network);
    await updateConfig(config);
  }

  Future<void> updateMinerSettings(MinerSettings miner) async {
    final config = state.copyWith(miner: miner);
    await updateConfig(config);
  }

  Future<void> updateCpuConfig(CpuConfig cpu) async {
    final config = state.copyWith(cpu: cpu);
    await updateConfig(config);
  }

  Future<void> updateGpuConfigs(List<GpuConfig> gpus) async {
    final config = state.copyWith(gpus: gpus);
    await updateConfig(config);
  }

  Future<void> updatePoolConfig(PoolConfig? pool) async {
    final config = state.copyWith(pool: pool);
    await updateConfig(config);
  }

  Future<void> updateUiConfig(UiConfig ui) async {
    final config = state.copyWith(ui: ui);
    await updateConfig(config);
  }

  Future<void> reset() async {
    state = MinerConfig.defaults();
    await _configService.save(state);
  }
}

/// RPC service provider (depends on config)
final rpcServiceProvider = Provider<RpcService>((ref) {
  final config = ref.watch(configProvider);
  return RpcService(rpcUrl: config.network.rpcUrl);
});

/// Chain ID provider
final chainIdProvider = FutureProvider<int>((ref) async {
  final rpc = ref.watch(rpcServiceProvider);
  try {
    return await rpc.getChainId();
  } catch (e) {
    // Return configured chain ID as fallback
    return ref.read(configProvider).network.chainId;
  }
});

/// Block height provider
final blockHeightProvider = FutureProvider<int>((ref) async {
  final rpc = ref.watch(rpcServiceProvider);
  return await rpc.getBlockNumber();
});

/// Sync status provider
final syncStatusProvider = FutureProvider((ref) async {
  final rpc = ref.watch(rpcServiceProvider);
  return await rpc.getSyncStatus();
});

/// Peer count provider
final peerCountProvider = FutureProvider<int>((ref) async {
  final rpc = ref.watch(rpcServiceProvider);
  return await rpc.getPeerCount();
});

/// Theme mode provider
final themeModeProvider = StateProvider<bool>((ref) => true); // true = dark mode
