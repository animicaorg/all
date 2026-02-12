/*
 * Animica Wallet — RPC Debug Panel
 *
 * Shows recent RPC calls with request/response details.
 * Helps debug balance fetching issues.
 */

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../services/rpc_debug.dart';

class RpcDebugPage extends StatefulWidget {
  const RpcDebugPage({super.key});

  @override
  State<RpcDebugPage> createState() => _RpcDebugPageState();
}

class _RpcDebugPageState extends State<RpcDebugPage> {
  @override
  Widget build(BuildContext context) {
    final entries = RpcDebugTracker.instance.entries;
    final stats = RpcDebugTracker.instance.stats;

    return Scaffold(
      appBar: AppBar(
        title: const Text('RPC Debug Panel'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => setState(() {}),
            tooltip: 'Refresh',
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline),
            onPressed: () {
              RpcDebugTracker.instance.clear();
              setState(() {});
            },
            tooltip: 'Clear history',
          ),
        ],
      ),
      body: Column(
        children: [
          // Stats summary
          Container(
            padding: const EdgeInsets.all(16),
            color: Theme.of(context).colorScheme.surfaceVariant,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _StatChip(
                  label: 'Total Calls',
                  value: '${stats['totalCalls']}',
                  icon: Icons.code,
                ),
                _StatChip(
                  label: 'Errors',
                  value: '${stats['errors']}',
                  icon: Icons.error_outline,
                  color: stats['errors'] > 0 ? Colors.red : null,
                ),
                _StatChip(
                  label: 'Avg Latency',
                  value: '${stats['avgLatencyMs']}ms',
                  icon: Icons.timer,
                ),
              ],
            ),
          ),

          // RPC calls list
          Expanded(
            child: entries.isEmpty
                ? const Center(
                    child: Text(
                      'No RPC calls yet.\nMake a balance request to see debug info.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.grey),
                    ),
                  )
                : ListView.builder(
                    itemCount: entries.length,
                    itemBuilder: (context, index) {
                      final entry = entries[index];
                      return _RpcCallCard(entry: entry);
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

class _StatChip extends StatelessWidget {
  final String label;
  final String value;
  final IconData icon;
  final Color? color;

  const _StatChip({
    required this.label,
    required this.value,
    required this.icon,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: color ?? Theme.of(context).colorScheme.primary),
        const SizedBox(height: 4),
        Text(
          value,
          style: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.bold,
            color: color,
          ),
        ),
        Text(
          label,
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}

class _RpcCallCard extends StatefulWidget {
  final RpcDebugEntry entry;

  const _RpcCallCard({required this.entry});

  @override
  State<_RpcCallCard> createState() => _RpcCallCardState();
}

class _RpcCallCardState extends State<_RpcCallCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final entry = widget.entry;
    final theme = Theme.of(context);
    final isError = entry.isError;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      color: isError
          ? theme.colorScheme.errorContainer.withOpacity(0.3)
          : null,
      child: Column(
        children: [
          ListTile(
            leading: Icon(
              isError ? Icons.error : Icons.check_circle,
              color: isError ? theme.colorScheme.error : Colors.green,
            ),
            title: Text(
              entry.method,
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            subtitle: Text(
              '${_formatTime(entry.timestamp)} • ${entry.latency.inMilliseconds}ms',
              style: theme.textTheme.bodySmall,
            ),
            trailing: IconButton(
              icon: Icon(_expanded ? Icons.expand_less : Icons.expand_more),
              onPressed: () => setState(() => _expanded = !_expanded),
            ),
          ),
          if (_expanded) ...[
            const Divider(),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _DetailRow('RPC URL', entry.rpcUrl),
                  const SizedBox(height: 8),
                  _DetailRow('Params', _formatJson(entry.params)),
                  const SizedBox(height: 8),
                  if (isError)
                    _DetailRow(
                      'Error',
                      '${entry.error}${entry.errorCode != null ? ' (code: ${entry.errorCode})' : ''}',
                      isError: true,
                    )
                  else
                    _DetailRow('Result', _formatJson(entry.result)),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          icon: const Icon(Icons.copy, size: 16),
                          label: const Text('Copy Request'),
                          onPressed: () => _copyToClipboard(
                            context,
                            json.encode({
                              'method': entry.method,
                              'params': entry.params,
                            }),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: OutlinedButton.icon(
                          icon: const Icon(Icons.copy, size: 16),
                          label: const Text('Copy Response'),
                          onPressed: () => _copyToClipboard(
                            context,
                            isError
                                ? json.encode({
                                    'error': entry.error,
                                    'code': entry.errorCode,
                                  })
                                : _formatJson(entry.result),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  String _formatTime(DateTime dt) {
    return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}:${dt.second.toString().padLeft(2, '0')}';
  }

  String _formatJson(dynamic value) {
    try {
      if (value == null) return 'null';
      if (value is String) return value;
      const encoder = JsonEncoder.withIndent('  ');
      return encoder.convert(value);
    } catch (_) {
      return value.toString();
    }
  }

  void _copyToClipboard(BuildContext context, String text) {
    Clipboard.setData(ClipboardData(text: text));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Copied to clipboard'),
        duration: Duration(seconds: 2),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  final String label;
  final String value;
  final bool isError;

  const _DetailRow(this.label, this.value, {this.isError = false});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: Theme.of(context).textTheme.labelMedium?.copyWith(
                fontWeight: FontWeight.bold,
              ),
        ),
        const SizedBox(height: 4),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: isError
                ? Theme.of(context).colorScheme.errorContainer.withOpacity(0.5)
                : Theme.of(context).colorScheme.surfaceVariant,
            borderRadius: BorderRadius.circular(8),
          ),
          child: SelectableText(
            value,
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: 12,
              color: isError ? Theme.of(context).colorScheme.error : null,
            ),
          ),
        ),
      ],
    );
  }
}
