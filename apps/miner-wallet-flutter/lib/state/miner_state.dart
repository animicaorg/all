/// Mining state providers
library;

import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:logging/logging.dart';

import '../models/mining_event.dart';
import '../models/device_info.dart';
import '../services/miner_service.dart';
import '../services/device_service.dart';
import 'app_state.dart';

/// Device service provider
final deviceServiceProvider = Provider<DeviceService>((ref) {
  return DeviceService();
});

/// Available devices provider
final devicesProvider = FutureProvider<List<DeviceInfo>>((ref) async {
  final deviceService = ref.watch(deviceServiceProvider);
  return await deviceService.detectDevices();
});

/// Miner service provider
final minerServiceProvider = Provider<MinerService>((ref) {
  return MinerService();
});

/// Mining status provider
final miningStatusProvider = StateNotifierProvider<MiningStatusNotifier, MiningStatus>((ref) {
  return MiningStatusNotifier(
    ref.watch(minerServiceProvider),
    ref.watch(configProvider),
  );
});

class MiningStatusNotifier extends StateNotifier<MiningStatus> {
  final MinerService _minerService;
  final MinerConfig _config;
  StreamSubscription<MiningEvent>? _eventSubscription;
  final _log = Logger('MiningStatusNotifier');

  MiningStatusNotifier(this._minerService, this._config) 
      : super(MiningStatus.stopped) {
    _subscribeToEvents();
  }

  void _subscribeToEvents() {
    _eventSubscription = _minerService.events.listen((event) {
      if (event.type == MiningEventType.statusChange) {
        state = event.status!;
      }
    });
  }

  Future<void> startMining() async {
    _log.info('Starting mining');
    final success = await _minerService.startMining(_config);
    if (!success) {
      _log.warning('Failed to start mining');
    }
  }

  Future<void> stopMining() async {
    _log.info('Stopping mining');
    await _minerService.stopMining();
  }

  Future<void> restartMining() async {
    _log.info('Restarting mining');
    await _minerService.restartMining(_config);
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    super.dispose();
  }
}

/// Hashrate provider
final hashrateProvider = StateNotifierProvider<HashrateNotifier, double>((ref) {
  return HashrateNotifier(ref.watch(minerServiceProvider));
});

class HashrateNotifier extends StateNotifier<double> {
  final MinerService _minerService;
  StreamSubscription<MiningEvent>? _eventSubscription;

  HashrateNotifier(this._minerService) : super(0.0) {
    _subscribeToEvents();
  }

  void _subscribeToEvents() {
    _eventSubscription = _minerService.events.listen((event) {
      if (event.type == MiningEventType.hashrateUpdate) {
        state = event.hashrate ?? 0.0;
      }
    });
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    super.dispose();
  }
}

/// Blocks found provider
final blocksFoundProvider = StateNotifierProvider<BlocksFoundNotifier, int>((ref) {
  return BlocksFoundNotifier(ref.watch(minerServiceProvider));
});

class BlocksFoundNotifier extends StateNotifier<int> {
  final MinerService _minerService;
  StreamSubscription<MiningEvent>? _eventSubscription;

  BlocksFoundNotifier(this._minerService) : super(0) {
    _subscribeToEvents();
  }

  void _subscribeToEvents() {
    _eventSubscription = _minerService.events.listen((event) {
      if (event.type == MiningEventType.blockFound) {
        state = event.count ?? 0;
      }
    });
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    super.dispose();
  }
}

/// Shares found provider
final sharesFoundProvider = StateNotifierProvider<SharesFoundNotifier, int>((ref) {
  return SharesFoundNotifier(ref.watch(minerServiceProvider));
});

class SharesFoundNotifier extends StateNotifier<int> {
  final MinerService _minerService;
  StreamSubscription<MiningEvent>? _eventSubscription;

  SharesFoundNotifier(this._minerService) : super(0) {
    _subscribeToEvents();
  }

  void _subscribeToEvents() {
    _eventSubscription = _minerService.events.listen((event) {
      if (event.type == MiningEventType.shareFound) {
        state = event.count ?? 0;
      }
    });
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    super.dispose();
  }
}

/// Mining logs provider
final miningLogsProvider = StateNotifierProvider<MiningLogsNotifier, List<String>>((ref) {
  return MiningLogsNotifier(ref.watch(minerServiceProvider));
});

class MiningLogsNotifier extends StateNotifier<List<String>> {
  final MinerService _minerService;
  StreamSubscription<MiningEvent>? _eventSubscription;
  static const int _maxLogs = 1000;

  MiningLogsNotifier(this._minerService) : super([]) {
    _subscribeToEvents();
  }

  void _subscribeToEvents() {
    _eventSubscription = _minerService.events.listen((event) {
      if (event.type == MiningEventType.log) {
        _addLog(event.message ?? '');
      } else if (event.type == MiningEventType.error) {
        _addLog('[ERROR] ${event.message ?? ''}');
      }
    });
  }

  void _addLog(String message) {
    final logs = [...state, message];
    // Keep only last N logs to prevent memory issues
    if (logs.length > _maxLogs) {
      state = logs.sublist(logs.length - _maxLogs);
    } else {
      state = logs;
    }
  }

  void clear() {
    state = [];
  }

  @override
  void dispose() {
    _eventSubscription?.cancel();
    super.dispose();
  }
}

/// Mining events stream provider
final miningEventsProvider = StreamProvider<MiningEvent>((ref) {
  final minerService = ref.watch(minerServiceProvider);
  return minerService.events;
});
