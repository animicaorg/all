/// Miner process management service
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:logging/logging.dart';
import 'package:process_run/process_run.dart';

import '../models/mining_event.dart';
import '../models/miner_config.dart';

class MinerService {
  final _log = Logger('MinerService');
  final _eventController = StreamController<MiningEvent>.broadcast();
  
  Process? _minerProcess;
  MiningStatus _status = MiningStatus.stopped;
  double _hashrate = 0.0;
  int _blocksFound = 0;
  int _sharesFound = 0;

  /// Stream of mining events
  Stream<MiningEvent> get events => _eventController.stream;

  /// Current mining status
  MiningStatus get status => _status;

  /// Current hashrate
  double get hashrate => _hashrate;

  /// Total blocks found
  int get blocksFound => _blocksFound;

  /// Total shares found
  int get sharesFound => _sharesFound;

  /// Start mining with the given configuration
  Future<bool> startMining(MinerConfig config) async {
    if (_status == MiningStatus.mining) {
      _log.warning('Mining already running');
      return false;
    }

    try {
      _updateStatus(MiningStatus.starting);

      // Find the miner executable
      final minerPath = await _findMinerExecutable();
      if (minerPath == null) {
        _log.severe('Miner executable not found');
        _emitError('Miner executable not found');
        _updateStatus(MiningStatus.stopped);
        return false;
      }

      // Build command arguments
      final args = _buildMinerArgs(config);

      _log.info('Starting miner: $minerPath ${args.join(" ")}');

      // Start the miner process
      _minerProcess = await Process.start(
        minerPath,
        args,
        mode: ProcessStartMode.normal,
      );

      // Listen to stdout
      _minerProcess!.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(_handleMinerOutput);

      // Listen to stderr
      _minerProcess!.stderr
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(_handleMinerError);

      // Wait for process exit
      _minerProcess!.exitCode.then((exitCode) {
        _log.info('Miner process exited with code $exitCode');
        if (_status != MiningStatus.stopped) {
          _updateStatus(MiningStatus.stopped);
          _emitError('Miner process exited unexpectedly (code: $exitCode)');
        }
      });

      _updateStatus(MiningStatus.mining);
      _log.info('Miner started successfully');
      return true;
    } catch (e, stackTrace) {
      _log.severe('Failed to start miner', e, stackTrace);
      _emitError('Failed to start miner: $e');
      _updateStatus(MiningStatus.stopped);
      return false;
    }
  }

  /// Stop mining
  Future<void> stopMining() async {
    if (_status == MiningStatus.stopped) {
      return;
    }

    try {
      _updateStatus(MiningStatus.stopping);
      
      if (_minerProcess != null) {
        _log.info('Stopping miner process');
        _minerProcess!.kill(ProcessSignal.sigterm);
        
        // Wait for graceful shutdown, with timeout
        await _minerProcess!.exitCode.timeout(
          const Duration(seconds: 5),
          onTimeout: () {
            _log.warning('Miner did not stop gracefully, forcing kill');
            _minerProcess!.kill(ProcessSignal.sigkill);
            return -1;
          },
        );
        
        _minerProcess = null;
      }

      _updateStatus(MiningStatus.stopped);
      _hashrate = 0.0;
      _log.info('Miner stopped');
    } catch (e, stackTrace) {
      _log.severe('Failed to stop miner', e, stackTrace);
      _updateStatus(MiningStatus.stopped);
    }
  }

  /// Restart mining with new configuration
  Future<bool> restartMining(MinerConfig config) async {
    await stopMining();
    await Future.delayed(const Duration(seconds: 1));
    return await startMining(config);
  }

  /// Find the miner executable in the system
  Future<String?> _findMinerExecutable() async {
    // Check common locations
    final possiblePaths = [
      './animica-miner',
      '../mining/animica-miner',
      '/usr/local/bin/animica-miner',
      '/usr/bin/animica-miner',
      Platform.environment['ANIMICA_MINER_PATH'],
    ];

    for (final path in possiblePaths) {
      if (path == null) continue;
      
      final file = File(path);
      if (await file.exists()) {
        return path;
      }
    }

    // Try to find in PATH
    try {
      final result = await Process.run('which', ['animica-miner']);
      if (result.exitCode == 0) {
        return result.stdout.toString().trim();
      }
    } catch (e) {
      // which command not available (Windows)
    }

    return null;
  }

  /// Build command line arguments for the miner
  List<String> _buildMinerArgs(MinerConfig config) {
    final args = <String>[
      '--rpc-url', config.network.rpcUrl,
      '--payout-address', config.miner.payoutAddress,
    ];

    // CPU configuration
    if (config.cpu.enabled) {
      args.addAll(['--cpu-threads', config.cpu.threads.toString()]);
    }

    // GPU configuration
    for (final gpu in config.gpus) {
      if (gpu.enabled) {
        args.addAll([
          '--gpu',
          '--gpu-device', gpu.deviceId.toString(),
          '--gpu-intensity', gpu.intensity.toString(),
        ]);
      }
    }

    // Pool configuration
    if (config.pool != null) {
      args.addAll([
        '--pool-url', config.pool!.url,
        if (config.pool!.username.isNotEmpty)
          '--pool-username', config.pool!.username,
      ]);
    }

    return args;
  }

  /// Handle miner stdout
  void _handleMinerOutput(String line) {
    _log.fine('Miner: $line');
    _emitLog(line);

    // Parse mining events from output
    if (line.contains('hashrate:')) {
      _parseHashrate(line);
    } else if (line.contains('share found') || line.contains('Share accepted')) {
      _sharesFound++;
      _emitShareFound();
    } else if (line.contains('block found') || line.contains('Block mined')) {
      _blocksFound++;
      _emitBlockFound();
    } else if (line.contains('template updated') || line.contains('New template')) {
      _emitTemplateUpdate();
    }
  }

  /// Handle miner stderr
  void _handleMinerError(String line) {
    _log.warning('Miner error: $line');
    _emitLog(line, isError: true);
    
    if (line.toLowerCase().contains('error') || 
        line.toLowerCase().contains('failed')) {
      _emitError(line);
    }
  }

  /// Parse hashrate from miner output
  void _parseHashrate(String line) {
    try {
      // Expected format: "hashrate: 125.5 MH/s" or similar
      final regex = RegExp(r'hashrate[:\s]+([0-9.]+)\s*([KMGT]?H/s)', 
          caseSensitive: false);
      final match = regex.firstMatch(line);
      
      if (match != null) {
        final value = double.parse(match.group(1)!);
        final unit = match.group(2)!.toUpperCase();
        
        // Convert to H/s
        double hashrate = value;
        if (unit.startsWith('K')) {
          hashrate = value * 1000;
        } else if (unit.startsWith('M')) {
          hashrate = value * 1000000;
        } else if (unit.startsWith('G')) {
          hashrate = value * 1000000000;
        } else if (unit.startsWith('T')) {
          hashrate = value * 1000000000000;
        }
        
        _hashrate = hashrate;
        _emitHashrateUpdate(hashrate);
      }
    } catch (e) {
      _log.fine('Failed to parse hashrate: $line');
    }
  }

  void _updateStatus(MiningStatus status) {
    _status = status;
    _eventController.add(MiningEvent.statusChange(status));
  }

  void _emitHashrateUpdate(double hashrate) {
    _eventController.add(MiningEvent.hashrateUpdate(hashrate));
  }

  void _emitShareFound() {
    _eventController.add(MiningEvent.shareFound(_sharesFound));
  }

  void _emitBlockFound() {
    _eventController.add(MiningEvent.blockFound(_blocksFound));
  }

  void _emitTemplateUpdate() {
    _eventController.add(MiningEvent.templateUpdate());
  }

  void _emitError(String message) {
    _eventController.add(MiningEvent.error(message));
  }

  void _emitLog(String message, {bool isError = false}) {
    _eventController.add(MiningEvent.log(message, isError: isError));
  }

  /// Dispose resources
  void dispose() {
    stopMining();
    _eventController.close();
  }
}
