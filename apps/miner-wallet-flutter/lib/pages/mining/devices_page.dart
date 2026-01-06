/// Devices page for configuring CPU and GPU mining devices
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/miner_state.dart';
import '../../state/app_state.dart';
import '../../widgets/device_card.dart';
import '../../models/device_info.dart';

class DevicesPage extends ConsumerWidget {
  const DevicesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final devicesAsync = ref.watch(devicesProvider);
    final config = ref.watch(configProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mining Devices'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              ref.invalidate(devicesProvider);
            },
            tooltip: 'Refresh devices',
          ),
        ],
      ),
      body: devicesAsync.when(
        data: (devices) {
          if (devices.isEmpty) {
            return const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.devices_other, size: 64, color: Colors.grey),
                  SizedBox(height: 16),
                  Text('No devices detected'),
                  SizedBox(height: 8),
                  Text(
                    'Check your hardware configuration',
                    style: TextStyle(color: Colors.grey),
                  ),
                ],
              ),
            );
          }

          return ListView.builder(
            padding: const EdgeInsets.all(16),
            itemCount: devices.length,
            itemBuilder: (context, index) {
              final device = devices[index];
              
              // Determine if device is enabled
              bool enabled = false;
              if (device is CpuInfo) {
                enabled = config.cpu.enabled;
              } else if (device is GpuInfo) {
                final gpu = device as GpuInfo;
                enabled = config.gpus.any(
                  (g) => g.deviceId == gpu.deviceId && g.enabled,
                );
              }

              return DeviceCard(
                device: device,
                enabled: enabled,
                onToggle: () => _toggleDevice(ref, device, enabled),
                onConfigure: () => _configureDevice(context, ref, device),
              );
            },
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, stack) => Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, size: 64, color: Colors.red),
              const SizedBox(height: 16),
              Text('Failed to detect devices'),
              const SizedBox(height: 8),
              Text(
                error.toString(),
                style: const TextStyle(color: Colors.grey),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _toggleDevice(WidgetRef ref, DeviceInfo device, bool currentlyEnabled) {
    final configNotifier = ref.read(configProvider.notifier);
    
    if (device is CpuInfo) {
      final cpu = ref.read(configProvider).cpu;
      configNotifier.updateCpuConfig(
        cpu.copyWith(enabled: !currentlyEnabled),
      );
    } else if (device is GpuInfo) {
      final gpu = device as GpuInfo;
      final gpus = ref.read(configProvider).gpus;
      
      if (currentlyEnabled) {
        // Remove or disable this GPU
        final updatedGpus = gpus.where((g) => g.deviceId != gpu.deviceId).toList();
        configNotifier.updateGpuConfigs(updatedGpus);
      } else {
        // Add this GPU with default settings
        final newGpu = GpuConfig(
          deviceId: gpu.deviceId,
          name: gpu.name,
          enabled: true,
          intensity: 5, // Default intensity
        );
        configNotifier.updateGpuConfigs([...gpus, newGpu]);
      }
    }
  }

  void _configureDevice(BuildContext context, WidgetRef ref, DeviceInfo device) {
    if (device is CpuInfo) {
      _showCpuConfigDialog(context, ref, device);
    } else if (device is GpuInfo) {
      _showGpuConfigDialog(context, ref, device);
    }
  }

  void _showCpuConfigDialog(BuildContext context, WidgetRef ref, CpuInfo cpu) {
    final config = ref.read(configProvider);
    final currentThreads = config.cpu.threads;

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('CPU Configuration'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Device: ${cpu.name}'),
            Text('Cores: ${cpu.cores}'),
            const SizedBox(height: 16),
            Text('Threads: $currentThreads'),
            Slider(
              value: currentThreads.toDouble(),
              min: 1,
              max: cpu.cores.toDouble(),
              divisions: cpu.cores - 1,
              label: currentThreads.toString(),
              onChanged: (value) {
                ref.read(configProvider.notifier).updateCpuConfig(
                  config.cpu.copyWith(threads: value.toInt()),
                );
              },
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  void _showGpuConfigDialog(BuildContext context, WidgetRef ref, GpuInfo gpu) {
    final config = ref.read(configProvider);
    final gpuConfig = config.gpus.firstWhere(
      (g) => g.deviceId == gpu.deviceId,
      orElse: () => GpuConfig(
        deviceId: gpu.deviceId,
        name: gpu.name,
        enabled: false,
        intensity: 5,
      ),
    );

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('GPU Configuration'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Device: ${gpu.name}'),
            if (gpu.memoryMb > 0) Text('Memory: ${gpu.memoryMb} MB'),
            const SizedBox(height: 16),
            Text('Intensity: ${gpuConfig.intensity}'),
            Slider(
              value: gpuConfig.intensity.toDouble(),
              min: 1,
              max: 10,
              divisions: 9,
              label: gpuConfig.intensity.toString(),
              onChanged: (value) {
                final gpus = config.gpus;
                final index = gpus.indexWhere((g) => g.deviceId == gpu.deviceId);
                if (index >= 0) {
                  final updated = [...gpus];
                  updated[index] = gpuConfig.copyWith(intensity: value.toInt());
                  ref.read(configProvider.notifier).updateGpuConfigs(updated);
                }
              },
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }
}
