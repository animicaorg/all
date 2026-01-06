/// Logs page for viewing mining logs
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/miner_state.dart';
import '../../widgets/log_viewer.dart';

class LogsPage extends ConsumerWidget {
  const LogsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final logs = ref.watch(miningLogsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mining Logs'),
      ),
      body: LogViewer(
        logs: logs,
        onClear: () {
          ref.read(miningLogsProvider.notifier).clear();
        },
      ),
    );
  }
}
