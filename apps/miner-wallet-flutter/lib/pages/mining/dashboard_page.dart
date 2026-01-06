import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class DashboardPage extends ConsumerWidget {
  const DashboardPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Mining Dashboard'),
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
                    _InfoRow(label: 'Chain ID', value: '--'),
                    _InfoRow(label: 'Block Height', value: '--'),
                    _InfoRow(label: 'Sync Status', value: '--'),
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
                    _InfoRow(label: 'Status', value: 'Stopped'),
                    _InfoRow(label: 'Hashrate', value: '0 H/s', 
                      valueStyle: Theme.of(context).textTheme.headlineMedium),
                    _InfoRow(label: 'Difficulty', value: '--'),
                    _InfoRow(label: 'Time to Block', value: '--'),
                    _InfoRow(label: 'Blocks Found', value: '0'),
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        ElevatedButton.icon(
                          onPressed: () {
                            // TODO: Start mining
                          },
                          icon: const Icon(Icons.play_arrow),
                          label: const Text('Start Mining'),
                        ),
                        const SizedBox(width: 8),
                        OutlinedButton.icon(
                          onPressed: null,
                          icon: const Icon(Icons.stop),
                          label: const Text('Stop'),
                        ),
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
