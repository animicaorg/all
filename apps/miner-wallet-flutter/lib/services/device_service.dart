/// Device detection service for CPU and GPU mining hardware
library;

import 'dart:io';
import 'package:logging/logging.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:universal_io/io.dart' as universal_io;

import '../models/device_info.dart';

class DeviceService {
  final _log = Logger('DeviceService');
  final _deviceInfo = DeviceInfoPlugin();

  /// Detect all available mining devices
  Future<List<DeviceInfo>> detectDevices() async {
    final devices = <DeviceInfo>[];

    // Always detect CPU
    final cpu = await detectCpu();
    if (cpu != null) {
      devices.add(cpu);
    }

    // Detect GPUs if available
    final gpus = await detectGpus();
    devices.addAll(gpus);

    _log.info('Detected ${devices.length} device(s)');
    return devices;
  }

  /// Detect CPU information
  Future<CpuInfo?> detectCpu() async {
    try {
      if (universal_io.Platform.isAndroid) {
        final androidInfo = await _deviceInfo.androidInfo;
        final cores = androidInfo.supportedAbis.length; // Approximation
        return CpuInfo(
          name: '${androidInfo.manufacturer} ${androidInfo.model}',
          cores: cores,
          threads: cores,
          capabilities: androidInfo.supportedAbis,
        );
      } else if (universal_io.Platform.isIOS) {
        final iosInfo = await _deviceInfo.iosInfo;
        return CpuInfo(
          name: iosInfo.utsname.machine,
          cores: 2, // iOS doesn't expose core count easily
          threads: 2,
          capabilities: const ['arm64'],
        );
      } else if (universal_io.Platform.isMacOS) {
        final macInfo = await _deviceInfo.macOsInfo;
        final cores = await _getMacCpuCores();
        return CpuInfo(
          name: macInfo.model,
          cores: cores,
          threads: cores,
          capabilities: [macInfo.arch],
        );
      } else if (universal_io.Platform.isWindows) {
        final winInfo = await _deviceInfo.windowsInfo;
        final cores = winInfo.numberOfCores;
        return CpuInfo(
          name: winInfo.computerName,
          cores: cores,
          threads: cores,
          capabilities: const ['x86_64'],
        );
      } else if (universal_io.Platform.isLinux) {
        final linuxInfo = await _deviceInfo.linuxInfo;
        final cores = await _getLinuxCpuCores();
        return CpuInfo(
          name: linuxInfo.name,
          cores: cores,
          threads: cores,
          capabilities: const ['x86_64'],
        );
      } else if (universal_io.Platform.environment.containsKey('FLUTTER_WEB')) {
        final webInfo = await _deviceInfo.webBrowserInfo;
        return CpuInfo(
          name: 'Web Browser (${webInfo.browserName})',
          cores: universal_io.Platform.numberOfProcessors,
          threads: universal_io.Platform.numberOfProcessors,
          capabilities: const ['wasm'],
        );
      }
    } catch (e, stackTrace) {
      _log.warning('Failed to detect CPU', e, stackTrace);
    }
    return null;
  }

  /// Detect GPU devices
  Future<List<GpuInfo>> detectGpus() async {
    final gpus = <GpuInfo>[];

    try {
      // Platform-specific GPU detection
      if (universal_io.Platform.isLinux || universal_io.Platform.isMacOS) {
        final detected = await _detectGpusUnix();
        gpus.addAll(detected);
      } else if (universal_io.Platform.isWindows) {
        final detected = await _detectGpusWindows();
        gpus.addAll(detected);
      }
    } catch (e, stackTrace) {
      _log.warning('Failed to detect GPUs', e, stackTrace);
    }

    return gpus;
  }

  Future<int> _getMacCpuCores() async {
    try {
      final result = await Process.run('sysctl', ['-n', 'hw.ncpu']);
      return int.parse(result.stdout.toString().trim());
    } catch (e) {
      return 2; // Default fallback
    }
  }

  Future<int> _getLinuxCpuCores() async {
    try {
      final result = await Process.run('nproc', []);
      return int.parse(result.stdout.toString().trim());
    } catch (e) {
      return 2; // Default fallback
    }
  }

  Future<List<GpuInfo>> _detectGpusUnix() async {
    final gpus = <GpuInfo>[];

    // Try lspci for Linux
    if (universal_io.Platform.isLinux) {
      try {
        final result = await Process.run('lspci', []);
        final lines = result.stdout.toString().split('\n');
        
        int deviceId = 0;
        for (final line in lines) {
          if (line.toLowerCase().contains('vga') || 
              line.toLowerCase().contains('3d') ||
              line.toLowerCase().contains('display')) {
            // Parse GPU name from lspci output
            final parts = line.split(':');
            if (parts.length >= 3) {
              final name = parts[2].trim();
              gpus.add(GpuInfo(
                deviceId: deviceId++,
                name: name,
                memoryMb: 0, // Would need additional query
                computeUnits: 0,
              ));
            }
          }
        }
      } catch (e) {
        _log.fine('lspci not available or failed: $e');
      }
    }

    // Try system_profiler for macOS
    if (universal_io.Platform.isMacOS) {
      try {
        final result = await Process.run(
          'system_profiler',
          ['SPDisplaysDataType', '-json'],
        );
        // Parse JSON output to extract GPU info
        // This is a simplified version
        final output = result.stdout.toString();
        if (output.contains('Chipset Model')) {
          gpus.add(GpuInfo(
            deviceId: 0,
            name: 'macOS GPU',
            memoryMb: 0,
            computeUnits: 0,
          ));
        }
      } catch (e) {
        _log.fine('system_profiler failed: $e');
      }
    }

    return gpus;
  }

  Future<List<GpuInfo>> _detectGpusWindows() async {
    final gpus = <GpuInfo>[];

    try {
      final result = await Process.run(
        'wmic',
        ['path', 'win32_VideoController', 'get', 'name'],
      );
      
      final lines = result.stdout.toString().split('\n');
      int deviceId = 0;
      
      for (final line in lines) {
        final name = line.trim();
        if (name.isNotEmpty && name != 'Name') {
          gpus.add(GpuInfo(
            deviceId: deviceId++,
            name: name,
            memoryMb: 0,
            computeUnits: 0,
          ));
        }
      }
    } catch (e) {
      _log.fine('wmic failed: $e');
    }

    return gpus;
  }
}
