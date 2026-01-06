import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/app_state.dart';
import '../../state/miner_state.dart';
import '../../utils/formatters.dart';

class DashboardPage extends ConsumerWidget {
  const DashboardPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final chainIdAsync = ref.watch(chainIdProvider);
    final blockHeightAsync = ref.watch(blockHeightProvider);
    final syncStatusAsync = ref.watch(syncStatusProvider);
    final miningStatus = ref.watch(miningStatusProvider);
    final hashrate = ref.watch(hashrateProvider);
    final blocksFound = ref.watch(blocksFoundProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mining Dashboard'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              ref.invalidate(chainIdProvider);
              ref.invalidate(blockHeightProvider);
              ref.invalidate(syncStatusProvider);
            },
            tooltip: 'Refresh',
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Chain Status Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Chain Status',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 16),
                    chainIdAsync.when(
                      data: (chainId) => _InfoRow(
                        label: 'Chain ID',
                        value: chainId.toString(),
                      ),
                      loading: () => _InfoRow(label: 'Chain ID', value: 'Loading...'),
                      error: (_, __) => _InfoRow(label: 'Chain ID', value: 'Error'),
                    ),
                    blockHeightAsync.when(
                      data: (height) => _InfoRow(
                        label: 'Block Height',
                        value: height.toString(),
                      ),
                      loading: () => _InfoRow(label: 'Block Height', value: 'Loading...'),
                      error: (_, __) => _InfoRow(label: 'Block Height', value: 'Error'),
                    ),
                    syncStatusAsync.when(
                      data: (status) => _InfoRow(
                        label: 'Sync Status',
                        value: status.syncing 
                            ? 'Syncing (${(status.progress! * 100).toStringAsFixed(1)}%)'
                            : 'Synced',
                      ),
                      loading: () => _InfoRow(label: 'Sync Status', value: 'Checking...'),
                      error: (_, __) => _InfoRow(label: 'Sync Status', value: 'Unknown'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            
            // Mining Status Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Mining Status',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 16),
                    _InfoRow(
                      label: 'Status',
                      value: miningStatus.displayName,
                      valueStyle: Theme.of(context).textTheme.bodyLarge?.copyWith(
                        color: miningStatus == MiningStatus.mining
                            ? Colors.green
                            : null,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    _InfoRow(
                      label: 'Hashrate',
                      value: formatHashrate(hashrate),
                      valueStyle: Theme.of(context).textTheme.headlineMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    _InfoRow(label: 'Blocks Found', value: blocksFound.toString()),
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        if (miningStatus == MiningStatus.stopped) ...[
                          ElevatedButton.icon(
                            onPressed: () {
                              ref.read(miningStatusProvider.notifier).startMining();
                            },
                            icon: const Icon(Icons.play_arrow),
                            label: const Text('Start Mining'),
                          ),
                        ] else ...[
                          OutlinedButton.icon(
                            onPressed: miningStatus == MiningStatus.mining
                                ? () {
                                    ref.read(miningStatusProvider.notifier).stopMining();
                                  }
                                : null,
                            icon: const Icon(Icons.stop),
                            label: const Text('Stop'),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;
  final TextStyle? valueStyle;

  const _InfoRow({
    required this.label,
    required this.value,
    this.valueStyle,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: Theme.of(context).textTheme.bodyMedium),
          Text(value, style: valueStyle ?? Theme.of(context).textTheme.bodyLarge),
        ],
      ),
    );
  }
}
