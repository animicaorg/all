import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../router.dart';
import '../../state/account_state.dart';
import '../../state/providers.dart';
import '../../utils/format.dart';
import '../../widgets/cards/balance_card.dart';

class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accounts = ref.watch(accountsStateProvider);
    final active = ref.watch(activeAccountProvider);
    final balance = ref.watch(activeBalanceProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Wallet'),
        actions: [
          IconButton(
            tooltip: 'Refresh balances',
            icon: const Icon(Icons.refresh),
            onPressed: () async {
              if (accounts.accounts.isEmpty) return;
              final notifier = ref.read(accountsStateProvider.notifier);
              await notifier.refreshAllBalances();
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Balances refreshed')),
                );
              }
            },
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _showAddAccountSheet(context, ref),
        icon: const Icon(Icons.add),
        label: const Text('Add account'),
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          if (active == null) return;
          await ref.read(accountsStateProvider.notifier).refreshBalance(active.address);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            if (accounts.error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: MaterialBanner(
                  backgroundColor: Theme.of(context).colorScheme.errorContainer,
                  content: Text(accounts.error!),
                  actions: [
                    TextButton(
                      onPressed: () => ref
                          .read(accountsStateProvider.notifier)
                          .state = accounts.copyWith(errorNull: true),
                      child: const Text('Dismiss'),
                    ),
                  ],
                ),
              ),
            if (active != null)
              _ActiveSummary(
                account: active,
                balance: balance,
                onSend: () => context.go(Routes.send),
                onReceive: () => context.go(Routes.receive),
                onRefresh: () =>
                    ref.read(accountsStateProvider.notifier).refreshBalance(active.address),
              )
            else
              const _EmptyHome(),
            const SizedBox(height: 20),
            _AccountsSection(
              accounts: accounts,
              active: active,
              onMakeActive: (addr) =>
                  ref.read(accountsStateProvider.notifier).setActive(addr),
              onRename: (addr) => _showRenameDialog(context, ref, addr),
              onRemove: (addr) => _confirmRemove(context, ref, addr),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showAddAccountSheet(BuildContext context, WidgetRef ref) async {
    final addrCtrl = TextEditingController();
    final labelCtrl = TextEditingController();
    String? error;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) {
        return Padding(
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 16,
            top: 8,
          ),
          child: StatefulBuilder(
            builder: (context, setState) {
              return Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Add account',
                      style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 12),
                  TextField(
                    controller: labelCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Label (optional)',
                      hintText: 'Main wallet',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: addrCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Address',
                      hintText: 'am1… or 0x…',
                    ),
                    minLines: 1,
                    maxLines: 3,
                    keyboardType: TextInputType.text,
                    autocorrect: false,
                  ),
                  if (error != null) ...[
                    const SizedBox(height: 8),
                    Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ],
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('Cancel'),
                      ),
                      const SizedBox(width: 12),
                      FilledButton.icon(
                        onPressed: () async {
                          final address = addrCtrl.text.trim();
                          if (address.isEmpty) {
                            setState(() => error = 'Address is required');
                            return;
                          }
                          try {
                            final notifier = ref.read(accountsStateProvider.notifier);
                            final norm = notifier.addAccount(
                              address: address,
                              label: labelCtrl.text.trim().isEmpty
                                  ? null
                                  : labelCtrl.text.trim(),
                            );
                            await notifier.refreshBalance(norm);
                            if (context.mounted) Navigator.pop(context);
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('Added $norm')),
                              );
                            }
                          } catch (e) {
                            setState(() => error = e.toString());
                          }
                        },
                        icon: const Icon(Icons.check),
                        label: const Text('Save'),
                      ),
                    ],
                  ),
                ],
              );
            },
          ),
        );
      },
    );
  }

  Future<void> _showRenameDialog(
      BuildContext context, WidgetRef ref, String address) async {
    final account = ref
        .read(accountsStateProvider)
        .accounts
        .firstWhere((a) => a.address == address);
    final ctrl = TextEditingController(text: account.label.isEmpty ? account.address : account.label);

    await showDialog(
      context: context,
      builder: (ctx) {
        return AlertDialog(
          title: const Text('Rename account'),
          content: TextField(
            controller: ctrl,
            decoration: const InputDecoration(labelText: 'Label'),
            autofocus: true,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                ref.read(accountsStateProvider.notifier).renameAccount(address, ctrl.text.trim());
                Navigator.pop(ctx);
              },
              child: const Text('Save'),
            ),
          ],
        );
      },
    );
  }

  Future<void> _confirmRemove(
      BuildContext context, WidgetRef ref, String address) async {
    await showDialog(
      context: context,
      builder: (ctx) {
        return AlertDialog(
          title: const Text('Remove account'),
          content: const Text('Remove this account from the device?'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: Theme.of(context).colorScheme.error,
              ),
              onPressed: () {
                ref.read(accountsStateProvider.notifier).removeAccount(address);
                Navigator.pop(ctx);
              },
              child: const Text('Remove'),
            ),
          ],
        );
      },
    );
  }
}

class _ActiveSummary extends StatelessWidget {
  const _ActiveSummary({
    required this.account,
    required this.balance,
    required this.onSend,
    required this.onReceive,
    required this.onRefresh,
  });

  final WalletAccount account;
  final AccountBalance? balance;
  final VoidCallback onSend;
  final VoidCallback onReceive;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final amount = balance?.amount ?? BigInt.zero;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        BalanceCard(
          symbol: 'ANM',
          decimals: 18,
          balanceUnits: amount.toString(),
          address: account.address,
          onRefresh: onRefresh,
          dense: false,
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: [
            Expanded(
              child: FilledButton.icon(
                onPressed: onSend,
                icon: const Icon(Icons.send_outlined),
                label: const Text('Send'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onReceive,
                icon: const Icon(Icons.qr_code_2_outlined),
                label: const Text('Receive'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          'Last updated: ${balance == null ? 'never' : formatTimestamp(balance!.lastUpdated)}',
          style: Theme.of(context)
              .textTheme
              .bodySmall
              ?.copyWith(color: Theme.of(context).textTheme.bodySmall?.color?.withOpacity(0.7)),
        ),
      ],
    );
  }
}

class _AccountsSection extends StatelessWidget {
  const _AccountsSection({
    required this.accounts,
    required this.active,
    required this.onMakeActive,
    required this.onRename,
    required this.onRemove,
  });

  final AccountsState accounts;
  final WalletAccount? active;
  final ValueChanged<String> onMakeActive;
  final ValueChanged<String> onRename;
  final ValueChanged<String> onRemove;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('Accounts', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(width: 8),
            if (accounts.busy) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
          ],
        ),
        const SizedBox(height: 8),
        if (accounts.accounts.isEmpty)
          const Text('Add an account to get started.')
        else
          Column(
            children: accounts.accounts.map((a) {
              final bal = accounts.balances[a.address];
              final isActive = active?.address == a.address;
              return Card(
                child: ListTile(
                  leading: Icon(isActive ? Icons.check_circle : Icons.account_balance_wallet_outlined,
                      color: isActive
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.onSurfaceVariant),
                  title: Text(a.label.isEmpty ? a.address : a.label,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(shortAddress(a.address)),
                      if (bal != null)
                        Text(
                          formatAmountWithSymbol(bal.amount, precision: 4),
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                    ],
                  ),
                  onTap: () => onMakeActive(a.address),
                  trailing: PopupMenuButton<String>(
                    onSelected: (value) {
                      switch (value) {
                        case 'active':
                          onMakeActive(a.address);
                          break;
                        case 'rename':
                          onRename(a.address);
                          break;
                        case 'remove':
                          onRemove(a.address);
                          break;
                      }
                    },
                    itemBuilder: (ctx) => [
                      const PopupMenuItem(value: 'active', child: Text('Set active')),
                      const PopupMenuItem(value: 'rename', child: Text('Rename')),
                      const PopupMenuItem(
                        value: 'remove',
                        child: Text('Remove'),
                      ),
                    ],
                  ),
                ),
              );
            }).toList(),
          ),
      ],
    );
  }
}

class _EmptyHome extends StatelessWidget {
  const _EmptyHome();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        const Icon(Icons.account_balance_wallet_outlined, size: 64),
        const SizedBox(height: 12),
        Text(
          'Add or import an account to start using your wallet.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        Text(
          'Your balances and quick actions will appear here once an account is added.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyMedium,
        ),
      ],
    );
  }
}
