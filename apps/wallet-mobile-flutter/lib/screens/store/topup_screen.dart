// Top up balance — the custodial ledger that funds subscription renewals.
//
// HONESTY: subscriptions on Animica are CUSTODIAL. Non-custodial pull payments
// are impossible, so recurring renewals debit an in-app balance (a custodial
// ledger) instead. This screen shows that balance and a personal deposit
// address: send ANM there and, once it finalizes on-chain, it's credited to the
// balance. The balance is withdrawable on-chain anytime.
//
// Data: GET /me/balance {balanceNanm, depositAddress}; if the balance route
// omits an address we mint one via POST /deposits/address. QR + copy mirror
// screens/receive.dart.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../state/store_state.dart';
import '../../state/wallet_state.dart';

/// Balance (may be unknown if /me/balance is unavailable) + a usable deposit
/// address (from the balance route, or minted via /deposits/address).
class _TopupInfo {
  final BigInt? balanceNanm;
  final String? address;
  const _TopupInfo({this.balanceNanm, this.address});
}

/// Resolves the custodial balance + a deposit address, tolerating a missing
/// /me/balance (we can still surface a deposit address to fund the ledger).
final _topupProvider = FutureProvider.autoDispose<_TopupInfo>((ref) async {
  final account = ref.watch(activeAccountProvider);
  if (account == null) return const _TopupInfo();
  final api = ref.watch(marketplaceApiProvider);
  BigInt? bal;
  String? addr;
  try {
    final b = await api.balance();
    bal = b.balanceNanm;
    addr = b.depositAddress;
  } on MarketplaceApiException {
    // Balance route may not be reachable yet; a deposit address is enough.
  }
  if (addr == null || addr.isEmpty) {
    addr = await api.depositAddress();
  }
  return _TopupInfo(balanceNanm: bal, address: addr);
});

class TopUpScreen extends ConsumerWidget {
  const TopUpScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(activeAccountProvider);
    final info = ref.watch(_topupProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Top up balance')),
      body: account == null
          ? const Center(child: Text('No active account.'))
          : info.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => _TopupError(
                message: _friendlyError(e),
                onRetry: () => ref.invalidate(_topupProvider),
              ),
              data: (d) => RefreshIndicator(
                onRefresh: () async => ref.refresh(_topupProvider.future),
                child: _TopupBody(info: d),
              ),
            ),
    );
  }
}

String _friendlyError(Object e) {
  if (e is MarketplaceApiException) {
    if (e.code == 'unsupported_scheme') {
      return 'Top up needs an ML-DSA-65 wallet to sign in to the store.';
    }
    if (e.isUnavailable) return 'The App Store is not available yet.';
    return e.message;
  }
  return 'Could not load your balance. Check your connection.';
}

class _TopupBody extends StatelessWidget {
  final _TopupInfo info;
  const _TopupBody({required this.info});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final address = info.address;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        _BalanceCard(balanceNanm: info.balanceNanm),
        const SizedBox(height: 20),
        Text('Add funds',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 4),
        Text(
          'Send ANM to this address to fund your subscriptions. It’s '
          'credited to your balance after the deposit finalizes on-chain. Your '
          'balance is custodial — renewals debit it, and you can withdraw '
          'it on-chain anytime.',
          style: TextStyle(color: cs.outline, fontSize: 13, height: 1.35),
        ),
        const SizedBox(height: 18),
        if (address == null || address.isEmpty)
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: cs.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              'No deposit address is available yet. Pull to refresh.',
              style: TextStyle(color: cs.outline),
            ),
          )
        else
          _DepositAddress(address: address),
      ],
    );
  }
}

class _BalanceCard extends StatelessWidget {
  final BigInt? balanceNanm;
  const _BalanceCard({required this.balanceNanm});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final b = balanceNanm;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: cs.primaryContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Store balance',
              style: TextStyle(color: cs.onPrimaryContainer, fontSize: 13)),
          const SizedBox(height: 6),
          Text(
            b == null ? '— ANM' : '${formatAnm(b)} ANM',
            style: TextStyle(
              color: cs.onPrimaryContainer,
              fontSize: 30,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 4),
          Text('Funds subscription renewals',
              style: TextStyle(
                  color: cs.onPrimaryContainer.withValues(alpha: 0.8),
                  fontSize: 12)),
        ],
      ),
    );
  }
}

class _DepositAddress extends StatelessWidget {
  final String address;
  const _DepositAddress({required this.address});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      children: [
        Center(
          child: Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: cs.surface,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: cs.outlineVariant),
            ),
            child: QrImageView(
              data: address,
              size: 240,
              backgroundColor: Colors.white,
              eyeStyle: const QrEyeStyle(
                eyeShape: QrEyeShape.square,
                color: Colors.black,
              ),
            ),
          ),
        ),
        const SizedBox(height: 18),
        SelectableText(
          address,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 16),
        FilledButton.tonalIcon(
          icon: const Icon(Icons.copy),
          label: const Text('Copy address'),
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: address));
            if (!context.mounted) return;
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Deposit address copied')),
            );
          },
        ),
      ],
    );
  }
}

class _TopupError extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _TopupError({required this.message, required this.onRetry});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.cloud_off, size: 48, color: cs.outline),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.tonalIcon(
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              onPressed: onRetry,
            ),
          ],
        ),
      ),
    );
  }
}
