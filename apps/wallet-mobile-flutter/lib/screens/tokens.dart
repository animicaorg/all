// Tokens — native ANM balance + per-watchlist ANM-20 balances.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/rpc.dart';
import '../services/tokens.dart';
import '../state/wallet_state.dart';

final _watchlistProvider = Provider<TokenWatchlist>((_) => TokenWatchlist());

final _watchedTokensProvider =
    FutureProvider.autoDispose<List<TokenSpec>>((ref) async {
  return ref.read(_watchlistProvider).load();
});

final _tokenBalancesProvider =
    FutureProvider.autoDispose<List<TokenBalance>>((ref) async {
  final acc = ref.watch(activeAccountProvider);
  if (acc == null) return const [];
  final list = await ref.watch(_watchedTokensProvider.future);
  if (list.isEmpty) return const [];
  final rpc = ref.read(rpcProvider);
  // Fetch in parallel — most ANM-20s have 3 view calls (balance + symbol +
  // decimals) so for 5 tokens we'd be 15 sequential RPCs without this.
  final results = await Future.wait(
    list.map((t) => fetchTokenBalance(rpc: rpc, holder: acc.address, spec: t)
        .catchError((_) => null)),
  );
  return results.whereType<TokenBalance>().toList(growable: false);
});

class TokensScreen extends ConsumerWidget {
  const TokensScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final acc = ref.watch(activeAccountProvider);
    final native = ref.watch(balanceProvider);
    final tokens = ref.watch(_tokenBalancesProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Tokens'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: 'Add token',
            onPressed: () => _showAddSheet(context, ref),
          ),
        ],
      ),
      body: acc == null
          ? const Center(child: Text('No active account.'))
          : RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(_watchedTokensProvider);
                ref.invalidate(_tokenBalancesProvider);
                await ref.read(balanceProvider.future);
              },
              child: ListView(
                padding: const EdgeInsets.all(12),
                children: [
                  _NativeTile(balance: native),
                  const SizedBox(height: 8),
                  tokens.when(
                    loading: () => const Padding(
                      padding: EdgeInsets.symmetric(vertical: 24),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                    error: (e, _) => _ErrorBox('Token fetch failed: $e'),
                    data: (list) {
                      if (list.isEmpty) {
                        return _EmptyTokens(onAdd: () => _showAddSheet(context, ref));
                      }
                      return Column(
                        children: list
                            .map((b) => _TokenTile(
                                balance: b,
                                onRemove: () async {
                                  await ref
                                      .read(_watchlistProvider)
                                      .remove(b.spec.contract);
                                  ref.invalidate(_watchedTokensProvider);
                                  ref.invalidate(_tokenBalancesProvider);
                                }))
                            .toList(),
                      );
                    },
                  ),
                ],
              ),
            ),
    );
  }

  Future<void> _showAddSheet(BuildContext context, WidgetRef ref) async {
    final contractCtrl = TextEditingController();
    final res = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (c) => Padding(
        padding:
            EdgeInsets.fromLTRB(20, 20, 20, MediaQuery.of(c).viewInsets.bottom + 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Add token',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            TextField(
              controller: contractCtrl,
              decoration: const InputDecoration(
                labelText: 'Contract address',
                hintText: 'anim1…  or 0x…',
                border: OutlineInputBorder(),
              ),
              autofocus: true,
            ),
            const SizedBox(height: 12),
            const Text(
              'The wallet will query the contract\'s balance_of(), symbol(), '
              'name() and decimals() views to populate the row.',
              style: TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: () => Navigator.pop(c, contractCtrl.text.trim()),
              child: const Text('Add'),
            ),
          ],
        ),
      ),
    );
    if (res == null || res.isEmpty) return;
    await ref.read(_watchlistProvider).add(TokenSpec(contract: res));
    ref.invalidate(_watchedTokensProvider);
    ref.invalidate(_tokenBalancesProvider);
  }
}

class _NativeTile extends StatelessWidget {
  final AsyncValue<BigInt> balance;
  const _NativeTile({required this.balance});

  @override
  Widget build(BuildContext context) {
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
        trailing: balance.when(
          loading: () => const Text('—'),
          error: (_, __) => const Text('?'),
          data: (n) =>
              Text(formatAnm(n), style: const TextStyle(fontWeight: FontWeight.w700)),
        ),
      ),
    );
  }
}

class _TokenTile extends StatelessWidget {
  final TokenBalance balance;
  final VoidCallback onRemove;
  const _TokenTile({required this.balance, required this.onRemove});

  @override
  Widget build(BuildContext context) {
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
          child: Text(
            balance.symbol.isNotEmpty ? balance.symbol[0].toUpperCase() : '?',
            style: TextStyle(
                color: Theme.of(context).colorScheme.onSecondaryContainer,
                fontWeight: FontWeight.w700),
          ),
        ),
        title: Text(balance.name ?? balance.symbol,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          '${balance.symbol} · ${balance.spec.contract.substring(0, 14)}…',
          style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
        ),
        trailing: Text(balance.formatted(),
            style: const TextStyle(fontWeight: FontWeight.w700)),
        onLongPress: onRemove,
      ),
    );
  }
}

class _EmptyTokens extends StatelessWidget {
  final VoidCallback onAdd;
  const _EmptyTokens({required this.onAdd});
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Icon(Icons.toll, color: Theme.of(context).colorScheme.outline),
          const SizedBox(height: 8),
          const Text('No ANM-20 tokens watched yet.',
              textAlign: TextAlign.center,
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            'Add a token by pasting its contract address.',
            textAlign: TextAlign.center,
            style: TextStyle(
                color: Theme.of(context).colorScheme.outline, fontSize: 12),
          ),
          const SizedBox(height: 12),
          OutlinedButton.icon(
            icon: const Icon(Icons.add),
            label: const Text('Add token'),
            onPressed: onAdd,
          ),
        ],
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  final String text;
  const _ErrorBox(this.text);
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(12),
        child: Text(text,
            style: TextStyle(color: Theme.of(context).colorScheme.error)),
      );
}
