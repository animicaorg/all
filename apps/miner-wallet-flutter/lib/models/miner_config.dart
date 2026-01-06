/// Configuration model for the miner-wallet application
library;

class MinerConfig {
  final NetworkConfig network;
  final MinerSettings miner;
  final CpuConfig cpu;
  final List<GpuConfig> gpus;
  final PoolConfig? pool;
  final UiConfig ui;

  const MinerConfig({
    required this.network,
    required this.miner,
    required this.cpu,
    this.gpus = const [],
    this.pool,
    required this.ui,
  });

  factory MinerConfig.defaults() {
    return MinerConfig(
      network: NetworkConfig.defaults(),
      miner: MinerSettings.defaults(),
      cpu: CpuConfig.defaults(),
      gpus: const [],
      pool: null,
      ui: UiConfig.defaults(),
    );
  }

  Map<String, dynamic> toJson() => {
        'network': network.toJson(),
        'miner': miner.toJson(),
        'cpu': cpu.toJson(),
        'gpus': gpus.map((g) => g.toJson()).toList(),
        if (pool != null) 'pool': pool!.toJson(),
        'ui': ui.toJson(),
      };

  factory MinerConfig.fromJson(Map<String, dynamic> json) {
    return MinerConfig(
      network: NetworkConfig.fromJson(json['network'] as Map<String, dynamic>),
      miner: MinerSettings.fromJson(json['miner'] as Map<String, dynamic>),
      cpu: CpuConfig.fromJson(json['cpu'] as Map<String, dynamic>),
      gpus: (json['gpus'] as List<dynamic>?)
              ?.map((g) => GpuConfig.fromJson(g as Map<String, dynamic>))
              .toList() ??
          [],
      pool: json['pool'] != null
          ? PoolConfig.fromJson(json['pool'] as Map<String, dynamic>)
          : null,
      ui: UiConfig.fromJson(json['ui'] as Map<String, dynamic>),
    );
  }

  MinerConfig copyWith({
    NetworkConfig? network,
    MinerSettings? miner,
    CpuConfig? cpu,
    List<GpuConfig>? gpus,
    PoolConfig? pool,
    UiConfig? ui,
  }) {
    return MinerConfig(
      network: network ?? this.network,
      miner: miner ?? this.miner,
      cpu: cpu ?? this.cpu,
      gpus: gpus ?? this.gpus,
      pool: pool ?? this.pool,
      ui: ui ?? this.ui,
    );
  }
}

class NetworkConfig {
  final String rpcUrl;
  final int chainId;
  final String networkName;

  const NetworkConfig({
    required this.rpcUrl,
    required this.chainId,
    required this.networkName,
  });

  factory NetworkConfig.defaults() {
    return const NetworkConfig(
      rpcUrl: 'http://127.0.0.1:8545',
      chainId: 1337,
      networkName: 'localnet',
    );
  }

  Map<String, dynamic> toJson() => {
        'rpc_url': rpcUrl,
        'chain_id': chainId,
        'network_name': networkName,
      };

  factory NetworkConfig.fromJson(Map<String, dynamic> json) {
    return NetworkConfig(
      rpcUrl: json['rpc_url'] as String,
      chainId: json['chain_id'] as int,
      networkName: json['network_name'] as String,
    );
  }

  NetworkConfig copyWith({String? rpcUrl, int? chainId, String? networkName}) {
    return NetworkConfig(
      rpcUrl: rpcUrl ?? this.rpcUrl,
      chainId: chainId ?? this.chainId,
      networkName: networkName ?? this.networkName,
    );
  }
}

class MinerSettings {
  final String payoutAddress;
  final bool autoStart;
  final int blocksPerBatch;
  final String mode; // 'solo' or 'pool'

  const MinerSettings({
    required this.payoutAddress,
    required this.autoStart,
    required this.blocksPerBatch,
    required this.mode,
  });

  factory MinerSettings.defaults() {
    return const MinerSettings(
      payoutAddress: '',
      autoStart: false,
      blocksPerBatch: 10,
      mode: 'solo',
    );
  }

  Map<String, dynamic> toJson() => {
        'payout_address': payoutAddress,
        'auto_start': autoStart,
        'blocks_per_batch': blocksPerBatch,
        'mode': mode,
      };

  factory MinerSettings.fromJson(Map<String, dynamic> json) {
    return MinerSettings(
      payoutAddress: json['payout_address'] as String,
      autoStart: json['auto_start'] as bool,
      blocksPerBatch: json['blocks_per_batch'] as int,
      mode: json['mode'] as String,
    );
  }

  MinerSettings copyWith({
    String? payoutAddress,
    bool? autoStart,
    int? blocksPerBatch,
    String? mode,
  }) {
    return MinerSettings(
      payoutAddress: payoutAddress ?? this.payoutAddress,
      autoStart: autoStart ?? this.autoStart,
      blocksPerBatch: blocksPerBatch ?? this.blocksPerBatch,
      mode: mode ?? this.mode,
    );
  }
}

class CpuConfig {
  final bool enabled;
  final int threads;

  const CpuConfig({
    required this.enabled,
    required this.threads,
  });

  factory CpuConfig.defaults() {
    return const CpuConfig(
      enabled: true,
      threads: 4,
    );
  }

  Map<String, dynamic> toJson() => {
        'enabled': enabled,
        'threads': threads,
      };

  factory CpuConfig.fromJson(Map<String, dynamic> json) {
    return CpuConfig(
      enabled: json['enabled'] as bool,
      threads: json['threads'] as int,
    );
  }

  CpuConfig copyWith({bool? enabled, int? threads}) {
    return CpuConfig(
      enabled: enabled ?? this.enabled,
      threads: threads ?? this.threads,
    );
  }
}

class GpuConfig {
  final int deviceId;
  final String name;
  final bool enabled;
  final int intensity;

  const GpuConfig({
    required this.deviceId,
    required this.name,
    required this.enabled,
    required this.intensity,
  });

  Map<String, dynamic> toJson() => {
        'device_id': deviceId,
        'name': name,
        'enabled': enabled,
        'intensity': intensity,
      };

  factory GpuConfig.fromJson(Map<String, dynamic> json) {
    return GpuConfig(
      deviceId: json['device_id'] as int,
      name: json['name'] as String,
      enabled: json['enabled'] as bool,
      intensity: json['intensity'] as int,
    );
  }

  GpuConfig copyWith({int? deviceId, String? name, bool? enabled, int? intensity}) {
    return GpuConfig(
      deviceId: deviceId ?? this.deviceId,
      name: name ?? this.name,
      enabled: enabled ?? this.enabled,
      intensity: intensity ?? this.intensity,
    );
  }
}

class PoolConfig {
  final String url;
  final String? username;

  const PoolConfig({
    required this.url,
    this.username,
  });

  Map<String, dynamic> toJson() => {
        'url': url,
        if (username != null) 'username': username,
      };

  factory PoolConfig.fromJson(Map<String, dynamic> json) {
    return PoolConfig(
      url: json['url'] as String,
      username: json['username'] as String?,
    );
  }

  PoolConfig copyWith({String? url, String? username}) {
    return PoolConfig(
      url: url ?? this.url,
      username: username ?? this.username,
    );
  }
}

class UiConfig {
  final bool systemTray;
  final bool notifications;
  final String logLevel;

  const UiConfig({
    required this.systemTray,
    required this.notifications,
    required this.logLevel,
  });

  factory UiConfig.defaults() {
    return const UiConfig(
      systemTray: true,
      notifications: true,
      logLevel: 'INFO',
    );
  }

  Map<String, dynamic> toJson() => {
        'system_tray': systemTray,
        'notifications': notifications,
        'log_level': logLevel,
      };

  factory UiConfig.fromJson(Map<String, dynamic> json) {
    return UiConfig(
      systemTray: json['system_tray'] as bool,
      notifications: json['notifications'] as bool,
      logLevel: json['log_level'] as String,
    );
  }

  UiConfig copyWith({bool? systemTray, bool? notifications, String? logLevel}) {
    return UiConfig(
      systemTray: systemTray ?? this.systemTray,
      notifications: notifications ?? this.notifications,
      logLevel: logLevel ?? this.logLevel,
    );
  }
}
