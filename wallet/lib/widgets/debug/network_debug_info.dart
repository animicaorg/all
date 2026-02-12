/*
 * Animica Wallet — Network Debug Info Widget
 *
 * Shows RPC URL, chain ID, and active address for debugging
 */

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../services/env.dart';
import '../../utils/format.dart';

class NetworkDebugInfo extends StatelessWidget {
  final String? activeAddress;
  final VoidCallback? onTap;

  const NetworkDebugInfo({
    super.key,
    this.activeAddress,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    
    return InkWell(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceVariant.withOpacity(0.5),
          border: Border(
            bottom: BorderSide(
              color: theme.colorScheme.outline.withOpacity(0.2),
              width: 1,
            ),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.info_outline,
                  size: 16,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 8),
                Text(
                  'Network Debug Info',
                  style: theme.textTheme.labelMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: theme.colorScheme.primary,
                  ),
                ),
                const Spacer(),
                if (onTap != null)
                  Icon(
                    Icons.arrow_forward_ios,
                    size: 12,
                    color: theme.colorScheme.outline,
                  ),
              ],
            ),
            const SizedBox(height: 8),
            _InfoRow(
              label: 'RPC URL',
              value: env.rpcHttp.toString(),
              onCopy: () => _copyToClipboard(context, env.rpcHttp.toString()),
            ),
            const SizedBox(height: 4),
            _InfoRow(
              label: 'Chain ID',
              value: '${env.chainId}',
            ),
            if (activeAddress != null) ...[
              const SizedBox(height: 4),
              _InfoRow(
                label: 'Active Address',
                value: formatAddress(activeAddress!, short: true),
                onCopy: () => _copyToClipboard(context, activeAddress!),
              ),
            ],
          ],
        ),
      ),
    );
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

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;
  final VoidCallback? onCopy;

  const _InfoRow({
    required this.label,
    required this.value,
    this.onCopy,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text(
          '$label: ',
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                fontWeight: FontWeight.w500,
              ),
        ),
        Expanded(
          child: Text(
            value,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  fontFamily: 'monospace',
                ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (onCopy != null)
          IconButton(
            icon: const Icon(Icons.copy, size: 14),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(),
            onPressed: onCopy,
            tooltip: 'Copy',
          ),
      ],
    );
  }
}
