/// Wallet setup page for importing or creating a wallet
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/wallet_state.dart';
import '../../utils/formatters.dart';

class WalletSetupPage extends ConsumerStatefulWidget {
  const WalletSetupPage({super.key});

  @override
  ConsumerState<WalletSetupPage> createState() => _WalletSetupPageState();
}

class _WalletSetupPageState extends ConsumerState<WalletSetupPage> {
  final _formKey = GlobalKey<FormState>();
  final _addressController = TextEditingController();
  final _privateKeyController = TextEditingController();
  bool _isImporting = true;

  @override
  void dispose() {
    _addressController.dispose();
    _privateKeyController.dispose();
    super.dispose();
  }

  Future<void> _handleImport() async {
    if (!_formKey.currentState!.validate()) {
      return;
    }

    final address = _addressController.text.trim();
    final privateKey = _privateKeyController.text.trim();

    try {
      await ref.read(importWalletProvider.notifier).importWallet(
        privateKey,
        address,
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Wallet imported successfully')),
        );
        context.go('/wallet');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to import wallet: $e'),
            backgroundColor: Theme.of(context).colorScheme.error,
          ),
        );
      }
    }
  }

  void _handleCreate() {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Wallet Creation Not Available'),
        content: const Text(
          'Wallet creation requires post-quantum cryptography (Dilithium3/SPHINCS+) '
          'which is not yet implemented in the Flutter app.\n\n'
          'Please use the CLI wallet to create a wallet:\n\n'
          '  animica wallet create\n\n'
          'Then import your wallet here using the address and private key.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final importState = ref.watch(importWalletProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Wallet Setup'),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Mode selector
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(
                  value: true,
                  label: Text('Import Wallet'),
                  icon: Icon(Icons.input),
                ),
                ButtonSegment(
                  value: false,
                  label: Text('Create New'),
                  icon: Icon(Icons.add),
                ),
              ],
              selected: {_isImporting},
              onSelectionChanged: (Set<bool> selection) {
                setState(() {
                  _isImporting = selection.first;
                });
              },
            ),
            const SizedBox(height: 24),

            if (_isImporting) ...[
              // Import form
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Import Existing Wallet',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'Enter your wallet credentials to import an existing wallet',
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: Theme.of(context).colorScheme.onSurfaceVariant,
                          ),
                        ),
                        const SizedBox(height: 24),
                        TextFormField(
                          controller: _addressController,
                          decoration: const InputDecoration(
                            labelText: 'Wallet Address',
                            hintText: 'anim1...',
                            border: OutlineInputBorder(),
                            prefixIcon: Icon(Icons.account_balance_wallet),
                          ),
                          validator: (value) {
                            if (value == null || value.isEmpty) {
                              return 'Please enter your wallet address';
                            }
                            if (!isValidAddress(value)) {
                              return 'Invalid address format';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: 16),
                        TextFormField(
                          controller: _privateKeyController,
                          decoration: const InputDecoration(
                            labelText: 'Private Key',
                            hintText: 'Enter your private key',
                            border: OutlineInputBorder(),
                            prefixIcon: Icon(Icons.key),
                          ),
                          obscureText: true,
                          validator: (value) {
                            if (value == null || value.isEmpty) {
                              return 'Please enter your private key';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: 24),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton.icon(
                            onPressed: importState.isLoading ? null : _handleImport,
                            icon: importState.isLoading
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.input),
                            label: Text(
                              importState.isLoading ? 'Importing...' : 'Import Wallet',
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Card(
                color: Theme.of(context).colorScheme.errorContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      Icon(
                        Icons.warning_outlined,
                        color: Theme.of(context).colorScheme.onErrorContainer,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'Never share your private key with anyone. Keep it safe and secure.',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context).colorScheme.onErrorContainer,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ] else ...[
              // Create wallet card
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Create New Wallet',
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                      const SizedBox(height: 16),
                      Text(
                        'Create a new wallet with post-quantum cryptography (Dilithium3).',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                      const SizedBox(height: 24),
                      SizedBox(
                        width: double.infinity,
                        child: ElevatedButton.icon(
                          onPressed: _handleCreate,
                          icon: const Icon(Icons.add),
                          label: const Text('Create Wallet'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Card(
                color: Theme.of(context).colorScheme.tertiaryContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      Icon(
                        Icons.info_outline,
                        color: Theme.of(context).colorScheme.onTertiaryContainer,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'Your new wallet will be secured with post-quantum resistant cryptography.',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context).colorScheme.onTertiaryContainer,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
