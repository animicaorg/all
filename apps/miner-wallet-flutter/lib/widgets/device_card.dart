/// Device card widget for displaying and configuring mining devices
library;

import 'package:flutter/material.dart';
import '../models/device_info.dart';

class DeviceCard extends StatelessWidget {
  final DeviceInfo device;
  final bool enabled;
  final VoidCallback? onToggle;
  final VoidCallback? onConfigure;

  const DeviceCard({
    super.key,
    required this.device,
    required this.enabled,
    this.onToggle,
    this.onConfigure,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    
    IconData icon;
    String subtitle;
    
    if (device is CpuInfo) {
      icon = Icons.memory;
      final cpu = device as CpuInfo;
      subtitle = '${cpu.cores} cores, ${cpu.threads} threads';
    } else if (device is GpuInfo) {
      icon = Icons.videogame_asset;
      final gpu = device as GpuInfo;
      subtitle = gpu.memoryMb > 0 
          ? '${gpu.memoryMb} MB, ${gpu.computeUnits} CUs'
          : 'GPU Device';
    } else {
      icon = Icons.device_unknown;
      subtitle = device.type.toString();
    }

    return Card(
      child: ListTile(
        leading: Icon(icon, size: 40, color: theme.colorScheme.primary),
        title: Text(
          device.name,
          style: theme.textTheme.titleMedium,
        ),
        subtitle: Text(subtitle),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (onConfigure != null)
              IconButton(
                icon: const Icon(Icons.settings),
                onPressed: onConfigure,
                tooltip: 'Configure',
              ),
            Switch(
              value: enabled,
              onChanged: onToggle != null ? (_) => onToggle!() : null,
            ),
          ],
        ),
      ),
    );
  }
}
