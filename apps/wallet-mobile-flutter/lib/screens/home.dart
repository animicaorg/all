// Home — main wallet view: balance, address chip, primary actions.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../services/price.dart';
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
            onRefresh: () async {
              ref.invalidate(anmMarketProvider);
              await ref.refresh(balanceProvider.future);
            },
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _BalanceCard(balance: balance),
                const SizedBox(height: 16),
                _AddressCard(address: active.address, label: active.label),
                const SizedBox(height: 16),
                _ActionRow(),
                const SizedBox(height: 12),
                Card(
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12)),
                  child: ListTile(
                    leading: const Icon(Icons.history),
                    title: const Text('Transaction history'),
                    subtitle: const Text(
                        'All transactions sent from this device'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/history'),
                  ),
                ),
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

class _BalanceCard extends ConsumerWidget {
  final AsyncValue<BigInt> balance;
  const _BalanceCard({required this.balance});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cs = Theme.of(context).colorScheme;
    // Null while the quote is loading or when NonKYC is unreachable — the
    // card then shows the ANM amount alone, exactly as before.
    final market = ref.watch(anmMarketProvider).value;
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
            data: (n) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                RichText(
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
                if (market != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    '≈ ${formatUsd(usdValueOfNanos(n, market.usdPerAnm))}',
                    style: TextStyle(
                        color: cs.onPrimary.withOpacity(0.92),
                        fontSize: 15,
                        fontWeight: FontWeight.w500),
                  ),
                ],
              ],
            ),
          ),
          if (market != null) ...[
            const SizedBox(height: 12),
            _PriceRow(market: market),
          ],
        ],
      ),
    );
  }
}

class _PriceRow extends StatelessWidget {
  final AnmMarket market;
  const _PriceRow({required this.market});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final change = market.changePct24h;
    final up = (change ?? 0) >= 0;
    final base = TextStyle(color: cs.onPrimary.withOpacity(0.85), fontSize: 12);
    return Row(
      children: [
        Text('1 ANM = ${formatUsd(market.usdPerAnm, sigFigs: 3)}', style: base),
        if (change != null) ...[
          const SizedBox(width: 8),
          Text(
            '${up ? '▲' : '▼'} ${change.abs().toStringAsFixed(1)}% 24h',
            style: TextStyle(
              // Readable tints on the primary gradient rather than raw
              // green/red, which vanish on some seed colors.
              color: up ? const Color(0xFFA5F3C4) : const Color(0xFFFFB4AB),
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
        const Spacer(),
        Text('NonKYC', style: base.copyWith(color: cs.onPrimary.withOpacity(0.55))),
      ],
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
