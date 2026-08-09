// Home — main wallet view: balance, address chip, primary actions.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../services/rpc.dart';
import '../state/wallet_state.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accountsAsync = ref.watch(accountsProvider);
    final active = ref.watch(activeAccountProvider);
    final balance = ref.watch(balanceProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Animica Wallet')),
      body: accountsAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Vault error: $e')),
        data: (accounts) {
          if (accounts.isEmpty) {
            return const _CreateFirstAccount();
          }
          if (active == null) {
            return const Center(child: Text('No active account selected.'));
          }
          return RefreshIndicator(
            onRefresh: () async => ref.refresh(balanceProvider.future),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _BalanceCard(balance: balance),
                const SizedBox(height: 16),
                _AddressCard(address: active.address, label: active.label),
                const SizedBox(height: 16),
                _ActionRow(),
                const SizedBox(height: 24),
                Text(
                  'Scheme',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: Theme.of(context).colorScheme.outline),
                ),
                const SizedBox(height: 4),
                Text(active.algName),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _BalanceCard extends StatelessWidget {
  final AsyncValue<BigInt> balance;
  const _BalanceCard({required this.balance});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [cs.primary, cs.primary.withOpacity(0.7)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Balance',
            style: TextStyle(color: cs.onPrimary.withOpacity(0.85), fontSize: 13),
          ),
          const SizedBox(height: 4),
          balance.when(
            loading: () => Text('— ANM', style: TextStyle(color: cs.onPrimary, fontSize: 28)),
            error: (_, __) => Text('?', style: TextStyle(color: cs.onPrimary, fontSize: 28)),
            data: (n) => RichText(
              text: TextSpan(
                style: TextStyle(color: cs.onPrimary, fontSize: 28, fontWeight: FontWeight.w600),
                children: [
                  TextSpan(text: formatAnm(n)),
                  const TextSpan(
                    text: ' ANM',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.normal),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _AddressCard extends StatelessWidget {
  final String address;
  final String label;
  const _AddressCard({required this.address, required this.label});

  @override
  Widget build(BuildContext context) {
    final shortAddr = '${address.substring(0, 10)}…${address.substring(address.length - 8)}';
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: InkWell(
        onTap: () async {
          await Clipboard.setData(ClipboardData(text: address));
          if (!context.mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Address copied'), duration: Duration(seconds: 1)),
          );
        },
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              const Icon(Icons.account_circle_outlined),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(shortAddr,
                        style:
                            const TextStyle(fontFamily: 'monospace', fontSize: 12)),
                  ],
                ),
              ),
              const Icon(Icons.copy, size: 18),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActionRow extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _ActionButton(
            icon: Icons.call_received,
            label: 'Receive',
            onTap: () => context.push('/receive'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _ActionButton(
            icon: Icons.call_made,
            label: 'Send',
            onTap: () => context.push('/send'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _ActionButton(
            icon: Icons.add_card,
            label: 'Buy',
            onTap: () => context.push('/buy'),
          ),
        ),
        const SizedBox(width: 12),
        // Pair with animica.xyz (or any Animica site) by scanning the QR code
        // it shows — the mobile counterpart of the browser extension.
        Expanded(
          child: _ActionButton(
            icon: Icons.qr_code_scanner,
            label: 'Connect',
            onTap: () => context.push('/connect'),
          ),
        ),
      ],
    );
  }
}

class _ActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _ActionButton({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return FilledButton.tonal(
      onPressed: onTap,
      style: FilledButton.styleFrom(
        padding: const EdgeInsets.symmetric(vertical: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      child: Column(
        children: [
          Icon(icon, size: 22),
          const SizedBox(height: 4),
          Text(label, style: const TextStyle(fontSize: 13)),
        ],
      ),
    );
  }
}

class _CreateFirstAccount extends ConsumerWidget {
  const _CreateFirstAccount();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.account_balance_wallet,
                size: 56, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 16),
            Text('Welcome to Animica Wallet', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              'Create a new ML-DSA-65 (FIPS 204) wallet to get started, or import one from your CLI / Chrome extension on the Settings tab.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Theme.of(context).colorScheme.outline),
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              icon: const Icon(Icons.add),
              label: const Text('Create new wallet'),
              // A bare `await` in a VoidCallback discards the failing Future to
              // the root zone — logcat only. That is why this button read as
              // completely dead rather than as an error.
              onPressed: () async {
                try {
                  await ref
                      .read(accountsProvider.notifier)
                      .createMlDsa65Account('Account 1');
                } catch (e) {
                  if (!context.mounted) return;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('Could not create wallet: $e')),
                  );
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}
