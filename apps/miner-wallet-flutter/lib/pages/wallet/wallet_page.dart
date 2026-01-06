import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/wallet_state.dart';
import '../../utils/formatters.dart';

class WalletPage extends ConsumerStatefulWidget {
  const WalletPage({super.key});

  @override
  ConsumerState<WalletPage> createState() => _WalletPageState();
}

class _WalletPageState extends ConsumerState<WalletPage> {
  final _toAddressController = TextEditingController();
  final _amountController = TextEditingController();
  final _formKey = GlobalKey<FormState>();

  @override
  void dispose() {
    _toAddressController.dispose();
    _amountController.dispose();
    super.dispose();
  }

  void _copyAddress(String? address) {
    if (address != null && address.isNotEmpty) {
      Clipboard.setData(ClipboardData(text: address));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Address copied to clipboard')),
      );
    }
  }

  void _refreshBalance() {
    // Invalidate the balance provider to trigger a refresh
    ref.invalidate(walletBalanceProvider);
    ref.invalidate(walletNonceProvider);
  }

  Future<void> _sendTransaction() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    final address = await ref.read(walletAddressProvider.future);
    if (address == null || address.isEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('No wallet configured')),
        );
      }
      return;
    }

    final toAddress = _toAddressController.text.trim();
    final amountStr = _amountController.text.trim();
    final amount = double.tryParse(amountStr);
    
    if (amount == null) {
      return;
    }

    // Convert ANM to base units (assuming 6 decimals)
    final valueInBaseUnits = (amount * 1000000).toInt();

    // Show confirmation dialog
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Confirm Transaction'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Send $amountStr ANM to:'),
            const SizedBox(height: 8),
            SelectableText(
              toAddress,
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 16),
            const Text(
              'This action cannot be undone.',
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey,
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );

    if (confirmed != true) {
      return;
    }

    try {
      await ref.read(sendTransactionProvider.notifier).sendTransaction(
        from: address,
        to: toAddress,
        value: valueInBaseUnits,
      );

      if (mounted) {
        final state = ref.read(sendTransactionProvider);
        state.when(
          data: (txHash) {
            if (txHash != null) {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('Transaction sent: ${truncateAddress(txHash)}'),
                  duration: const Duration(seconds: 5),
                ),
              );
              _toAddressController.clear();
              _amountController.clear();
              _refreshBalance();
            }
          },
          loading: () {},
          error: (error, _) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text('Transaction failed: ${error.toString()}'),
                backgroundColor: Theme.of(context).colorScheme.error,
                duration: const Duration(seconds: 5),
              ),
            );
          },
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Theme.of(context).colorScheme.error,
            duration: const Duration(seconds: 5),
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final addressAsync = ref.watch(walletAddressProvider);
    final balanceAsync = ref.watch(walletBalanceProvider);
    final nonceAsync = ref.watch(walletNonceProvider);
    final sendTxState = ref.watch(sendTransactionProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Wallet'),
        actions: [
          IconButton(
            icon: const Icon(Icons.history),
            tooltip: 'Transaction History',
            onPressed: () => context.go('/transaction-history'),
          ),
          IconButton(
            icon: const Icon(Icons.qr_code),
            tooltip: 'Receive',
            onPressed: () => context.go('/receive'),
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Wallet Info Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Wallet Information',
                      style: Theme.of(context).textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 16),
                    
                    // Address
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Address',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                              const SizedBox(height: 4),
                              addressAsync.when(
                                data: (address) => Text(
                                  address != null && address.isNotEmpty 
                                    ? truncateAddress(address)
                                    : 'Not configured',
                                  style: Theme.of(context).textTheme.bodyMedium,
                                ),
                                loading: () => const Text('Loading...'),
                                error: (_, __) => const Text('Error loading address'),
                              ),
                            ],
                          ),
                        ),
                        addressAsync.when(
                          data: (address) => IconButton(
                            onPressed: address != null && address.isNotEmpty
                              ? () => _copyAddress(address)
                              : null,
                            icon: const Icon(Icons.copy),
                            tooltip: 'Copy address',
                          ),
                          loading: () => const IconButton(
                            onPressed: null,
                            icon: Icon(Icons.copy),
                          ),
                          error: (_, __) => const IconButton(
                            onPressed: null,
                            icon: Icon(Icons.copy),
                          ),
                        ),
                      ],
                    ),
                    const Divider(height: 32),
                    
                    // Balance
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Balance',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                              const SizedBox(height: 4),
                              balanceAsync.when(
                                data: (balance) => Text(
                                  formatAnm(balance),
                                  style: Theme.of(context).textTheme.headlineMedium,
                                ),
                                loading: () => const CircularProgressIndicator(),
                                error: (_, __) => Text(
                                  'Error',
                                  style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                                    color: Theme.of(context).colorScheme.error,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        OutlinedButton.icon(
                          onPressed: _refreshBalance,
                          icon: const Icon(Icons.refresh),
                          label: const Text('Refresh'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    
                    // Nonce
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          'Nonce',
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                        nonceAsync.when(
                          data: (nonce) => Text(
                            nonce.toString(),
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          loading: () => const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                          error: (_, __) => const Text('--'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            
            // Send Transaction Card
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Send Transaction',
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _toAddressController,
                        decoration: const InputDecoration(
                          labelText: 'To Address',
                          hintText: 'anim1...',
                          border: OutlineInputBorder(),
                        ),
                        validator: (value) {
                          if (value == null || value.isEmpty) {
                            return 'Please enter an address';
                          }
                          if (!isValidAddress(value)) {
                            return 'Invalid address format';
                          }
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _amountController,
                        decoration: const InputDecoration(
                          labelText: 'Amount (ANM)',
                          hintText: '0.0',
                          border: OutlineInputBorder(),
                        ),
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        validator: (value) {
                          if (value == null || value.isEmpty) {
                            return 'Please enter an amount';
                          }
                          final amount = double.tryParse(value);
                          if (amount == null || amount <= 0) {
                            return 'Please enter a valid amount';
                          }
                          return null;
                        },
                      ),
                      const SizedBox(height: 16),
                      addressAsync.when(
                        data: (address) => ElevatedButton.icon(
                          onPressed: (address != null && address.isNotEmpty && 
                                      !sendTxState.isLoading)
                            ? _sendTransaction
                            : null,
                          icon: sendTxState.isLoading
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.send),
                          label: Text(sendTxState.isLoading ? 'Sending...' : 'Send'),
                        ),
                        loading: () => const ElevatedButton.icon(
                          onPressed: null,
                          icon: Icon(Icons.send),
                          label: Text('Send'),
                        ),
                        error: (_, __) => const ElevatedButton.icon(
                          onPressed: null,
                          icon: Icon(Icons.send),
                          label: Text('Send'),
                        ),
                      ),
                      if (address == null || (addressAsync.value?.isEmpty ?? true))
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(
                            'Please configure a wallet to send transactions',
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                              color: Theme.of(context).colorScheme.error,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
