// Tokens — ANM-20 balances for the active wallet.
//
// v0.1: lists known/watched token contracts (configurable per address)
// and queries each contract's `balance_of(address)` view via state.call.
// Since dynamic contract discovery (which tokens does this wallet hold?)
// requires an indexer, watched-tokens for v0.1 is hardcoded; UX adds a
// "+ Add token" flow in v0.2.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/wallet_state.dart';

class TokensScreen extends ConsumerWidget {
  const TokensScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final acc = ref.watch(activeAccountProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tokens'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: 'Add token (v0.2)',
            onPressed: () {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Add-token flow coming in v0.2.')),
              );
            },
          ),
        ],
      ),
      body: acc == null
          ? const Center(child: Text('No active account.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _NativeBalanceTile(),
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color:
                        Theme.of(context).colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline,
                          size: 18,
                          color: Theme.of(context).colorScheme.outline),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'ANM-20 token discovery + balance fetches land in v0.2. '
                          'Use the explorer (explorer.animica.org) to inspect token contracts in the meantime.',
                          style: TextStyle(
                              color: Theme.of(context).colorScheme.outline,
                              fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
    );
  }
}

class _NativeBalanceTile extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bal = ref.watch(balanceProvider);
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: Theme.of(context).colorScheme.primary,
          child: Text('A',
              style: TextStyle(
                  color: Theme.of(context).colorScheme.onPrimary,
                  fontWeight: FontWeight.w700)),
        ),
        title: const Text('Animica',
            style: TextStyle(fontWeight: FontWeight.w600)),
        subtitle: const Text('Native ANM'),
        trailing: bal.when(
          loading: () => const Text('—'),
          error: (_, __) => const Text('?'),
          data: (n) {
            final whole = n ~/ BigInt.from(1000000000);
            final frac = n % BigInt.from(1000000000);
            String s;
            if (frac == BigInt.zero) {
              s = whole.toString();
            } else {
              final f = frac.toString().padLeft(9, '0').substring(0, 4);
              s = '$whole.${f.replaceAll(RegExp(r'0+\$'), '')}';
            }
            return Text(s,
                style: const TextStyle(fontWeight: FontWeight.w700));
          },
        ),
      ),
    );
  }
}
