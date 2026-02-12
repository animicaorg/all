/*
 * Animica Wallet — Balance Check Dialog
 *
 * Manual balance check tool that shows exact RPC request/response
 */

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../services/state_service.dart';

class BalanceCheckDialog extends StatefulWidget {
  final String address;
  final StateService stateService;

  const BalanceCheckDialog({
    super.key,
    required this.address,
    required this.stateService,
  });

  @override
  State<BalanceCheckDialog> createState() => _BalanceCheckDialogState();
}

class _BalanceCheckDialogState extends State<BalanceCheckDialog> {
  bool _loading = false;
  String? _request;
  String? _response;
  String? _parsed;
  String? _error;

  @override
  void initState() {
    super.initState();
    _checkBalance();
  }

  Future<void> _checkBalance() async {
    setState(() {
      _loading = true;
      _request = null;
      _response = null;
      _parsed = null;
      _error = null;
    });

    try {
      // Build request info
      _request = json.encode({
        'jsonrpc': '2.0',
        'method': 'animica_getBalance',
        'params': [widget.address, 'latest'],
        'id': 1,
      });

      final balance = await widget.stateService.getBalance(widget.address);
      final nonce = await widget.stateService.getNonce(widget.address);

      setState(() {
        _response = json.encode({
          'balance': balance.toString(),
          'nonce': nonce,
        });
        _parsed = 'Balance: $balance\nNonce: $nonce\n\nFormatted: ${_formatBalance(balance)}';
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  String _formatBalance(BigInt amount) {
    // Format with 18 decimals
    const decimals = 18;
    final str = amount.toString();
    if (str.length <= decimals) {
      final padded = str.padLeft(decimals, '0');
      final frac = padded.replaceFirst(RegExp(r'0+$'), '');
      return '0.${frac.isEmpty ? '0' : frac} ANM';
    } else {
      final whole = str.substring(0, str.length - decimals);
      final frac = str.substring(str.length - decimals).replaceFirst(RegExp(r'0+$'), '');
      return '$whole.${frac.isEmpty ? '0' : frac} ANM';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      child: Container(
        constraints: const BoxConstraints(maxWidth: 600, maxHeight: 700),
        child: Column(
          children: [
            AppBar(
              title: const Text('Balance Check'),
              automaticallyImplyLeading: false,
              actions: [
                IconButton(
                  icon: const Icon(Icons.refresh),
                  onPressed: _loading ? null : _checkBalance,
                  tooltip: 'Retry',
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    'Address',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 4),
                  _InfoBox(widget.address),
                  const SizedBox(height: 16),
                  Text(
                    'RPC Request',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 4),
                  if (_request != null) _InfoBox(_request!),
                  const SizedBox(height: 16),
                  if (_loading)
                    const Center(child: CircularProgressIndicator())
                  else if (_error != null) ...[
                    Text(
                      'Error',
                      style: Theme.of(context).textTheme.titleSmall?.copyWith(
                            color: Theme.of(context).colorScheme.error,
                          ),
                    ),
                    const SizedBox(height: 4),
                    _InfoBox(_error!, isError: true),
                  ] else if (_response != null) ...[
                    Text(
                      'RPC Response',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 4),
                    _InfoBox(_response!),
                    const SizedBox(height: 16),
                    Text(
                      'Parsed Result',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: 4),
                    _InfoBox(_parsed!),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoBox extends StatelessWidget {
  final String text;
  final bool isError;

  const _InfoBox(this.text, {this.isError = false});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isError
            ? Theme.of(context).colorScheme.errorContainer.withOpacity(0.5)
            : Theme.of(context).colorScheme.surfaceVariant,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: SelectableText(
              text,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 12,
                color: isError ? Theme.of(context).colorScheme.error : null,
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.copy, size: 16),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: text));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('Copied to clipboard'),
                  duration: Duration(seconds: 2),
                ),
              );
            },
            tooltip: 'Copy',
          ),
        ],
      ),
    );
  }
}
