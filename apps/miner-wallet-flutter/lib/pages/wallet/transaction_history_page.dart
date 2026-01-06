/// Transaction history page
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../utils/formatters.dart';

class TransactionHistoryPage extends ConsumerWidget {
  const TransactionHistoryPage({super.key});

  @override
  Widget build(BuildContext context) {
    // Placeholder: In a real implementation, this would fetch transactions
    // from the RPC service using eth_getBlockByNumber and filter by address
    final transactions = <Transaction>[];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Transaction History'),
      ),
      body: transactions.isEmpty
        ? Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  Icons.receipt_long_outlined,
                  size: 64,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
                const SizedBox(height: 16),
                Text(
                  'No Transactions Yet',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 8),
                Text(
                  'Your transaction history will appear here',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 24),
                Card(
                  color: Theme.of(context).colorScheme.tertiaryContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(
                              Icons.info_outline,
                              color: Theme.of(context).colorScheme.onTertiaryContainer,
                              size: 20,
                            ),
                            const SizedBox(width: 8),
                            Text(
                              'Transaction History Coming Soon',
                              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                                color: Theme.of(context).colorScheme.onTertiaryContainer,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'Transaction history requires additional RPC methods to query '
                          'historical transactions from the blockchain. This feature will '
                          'be implemented in a future update.\n\n'
                          'For now, you can view your balance and send transactions.',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context).colorScheme.onTertiaryContainer,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          )
        : ListView.builder(
            itemCount: transactions.length,
            itemBuilder: (context, index) {
              final tx = transactions[index];
              return TransactionListItem(transaction: tx);
            },
          ),
    );
  }
}

/// Transaction model (placeholder)
class Transaction {
  final String hash;
  final String from;
  final String to;
  final int value;
  final DateTime timestamp;
  final bool isIncoming;
  final String? status; // 'pending', 'confirmed', 'failed'

  Transaction({
    required this.hash,
    required this.from,
    required this.to,
    required this.value,
    required this.timestamp,
    required this.isIncoming,
    this.status = 'confirmed',
  });
}

/// Transaction list item widget
class TransactionListItem extends StatelessWidget {
  final Transaction transaction;

  const TransactionListItem({
    required this.transaction,
    super.key,
  });

  @override
  Widget build(BuildContext context) {
    final isIncoming = transaction.isIncoming;
    final icon = isIncoming ? Icons.call_received : Icons.call_made;
    final color = isIncoming
      ? Theme.of(context).colorScheme.primary
      : Theme.of(context).colorScheme.secondary;

    return ListTile(
      leading: CircleAvatar(
        backgroundColor: color.withOpacity(0.1),
        child: Icon(icon, color: color),
      ),
      title: Text(
        isIncoming ? 'Received' : 'Sent',
        style: Theme.of(context).textTheme.titleMedium,
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            isIncoming
              ? 'From: ${truncateAddress(transaction.from)}'
              : 'To: ${truncateAddress(transaction.to)}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 2),
          Text(
            _formatTimestamp(transaction.timestamp),
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
        ],
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text(
            '${isIncoming ? '+' : '-'}${formatAnm(transaction.value)}',
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
              color: color,
              fontWeight: FontWeight.bold,
            ),
          ),
          if (transaction.status == 'pending')
            const SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
        ],
      ),
      onTap: () {
        // Show transaction details
        showDialog(
          context: context,
          builder: (context) => TransactionDetailsDialog(transaction: transaction),
        );
      },
    );
  }

  String _formatTimestamp(DateTime timestamp) {
    final now = DateTime.now();
    final difference = now.difference(timestamp);

    if (difference.inMinutes < 1) {
      return 'Just now';
    } else if (difference.inHours < 1) {
      return '${difference.inMinutes}m ago';
    } else if (difference.inDays < 1) {
      return '${difference.inHours}h ago';
    } else if (difference.inDays < 7) {
      return '${difference.inDays}d ago';
    } else {
      return '${timestamp.month}/${timestamp.day}/${timestamp.year}';
    }
  }
}

/// Transaction details dialog
class TransactionDetailsDialog extends StatelessWidget {
  final Transaction transaction;

  const TransactionDetailsDialog({
    required this.transaction,
    super.key,
  });

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Transaction Details'),
      content: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            _buildDetailRow(context, 'Hash', transaction.hash),
            const Divider(),
            _buildDetailRow(context, 'From', transaction.from),
            const Divider(),
            _buildDetailRow(context, 'To', transaction.to),
            const Divider(),
            _buildDetailRow(context, 'Amount', formatAnm(transaction.value)),
            const Divider(),
            _buildDetailRow(
              context,
              'Status',
              transaction.status?.toUpperCase() ?? 'CONFIRMED',
            ),
            const Divider(),
            _buildDetailRow(
              context,
              'Time',
              transaction.timestamp.toString(),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Close'),
        ),
      ],
    );
  }

  Widget _buildDetailRow(BuildContext context, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 4),
          SelectableText(
            value,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ],
      ),
    );
  }
}
