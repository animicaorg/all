// Subscriptions — manage the buyer's CUSTODIAL recurring subscriptions.
//
// Data: GET /store/subscriptions via mySubscriptionsProvider. Each row shows the
// app, a lifecycle chip (Active / Renewal failed / Cancelled / Expired), the
// next-renewal (or access-until) date, the per-period price, and a Cancel
// action for still-renewing subscriptions. When any subscription is in its
// grace/dunning window (graceUntil in the future) a banner nudges a top-up.
//
// Honesty: renewals debit the in-app Store balance (custodial). Cancel stops
// future renewals; access continues to the end of the paid period.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../state/store_state.dart';
import '../../state/wallet_state.dart';

class SubscriptionsScreen extends ConsumerWidget {
  const SubscriptionsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(activeAccountProvider);
    final subs = ref.watch(mySubscriptionsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Subscriptions'),
        actions: [
          IconButton(
            icon: const Icon(Icons.account_balance_wallet_outlined),
            tooltip: 'Top up balance',
            onPressed: () => context.push('/store/topup'),
          ),
        ],
      ),
      body: account == null
          ? const Center(child: Text('No active account.'))
          : subs.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => _SubsError(
                message: _friendlyError(e),
                onRetry: () => ref.invalidate(mySubscriptionsProvider),
              ),
              data: (items) {
                if (items.isEmpty) return const _EmptySubs();
                final inGrace = items.any((s) => s.isInGrace);
                return RefreshIndicator(
                  onRefresh: () async =>
                      ref.refresh(mySubscriptionsProvider.future),
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      if (inGrace) const _GraceBanner(),
                      for (final s in items)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: _SubscriptionTile(sub: s),
                        ),
                      const SizedBox(height: 8),
                      const _CustodialFootnote(),
                    ],
                  ),
                );
              },
            ),
    );
  }
}

String _friendlyError(Object e) {
  if (e is MarketplaceApiException) {
    if (e.code == 'unsupported_scheme') {
      return 'Subscriptions need an ML-DSA-65 wallet to sign in to the store.';
    }
    if (e.isNotFound || e.isUnavailable) {
      return 'The App Store is not available yet.';
    }
    return e.message;
  }
  return 'Could not load your subscriptions. Check your connection.';
}

String _fmtDate(DateTime? d) {
  if (d == null) return '—';
  final l = d.toLocal();
  final m = l.month.toString().padLeft(2, '0');
  final day = l.day.toString().padLeft(2, '0');
  return '${l.year}-$m-$day';
}

class _GraceBanner extends StatelessWidget {
  const _GraceBanner();
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: cs.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(Icons.warning_amber_rounded, color: cs.onErrorContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Renewal failed',
                    style: TextStyle(
                        color: cs.onErrorContainer,
                        fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(
                  'Your Store balance couldn’t cover a renewal. Top up to keep '
                  'your subscription active.',
                  style: TextStyle(
                      color: cs.onErrorContainer, fontSize: 12, height: 1.3),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          FilledButton.tonal(
            onPressed: () => context.push('/store/topup'),
            child: const Text('Top up'),
          ),
        ],
      ),
    );
  }
}

class _SubscriptionTile extends ConsumerStatefulWidget {
  final PurchaseRecord sub;
  const _SubscriptionTile({required this.sub});

  @override
  ConsumerState<_SubscriptionTile> createState() => _SubscriptionTileState();
}

class _SubscriptionTileState extends ConsumerState<_SubscriptionTile> {
  bool _cancelling = false;

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancel subscription?'),
        content: const Text(
          'Auto-renewal stops. You keep access until the end of the period '
          'you already paid for. No refund for the current period.',
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(c).pop(false),
              child: const Text('Keep')),
          FilledButton(
              onPressed: () => Navigator.of(c).pop(true),
              child: const Text('Cancel subscription')),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _cancelling = true);
    try {
      final api = ref.read(marketplaceApiProvider);
      await api.cancelSubscription(widget.sub.id);
      ref.invalidate(mySubscriptionsProvider);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Subscription cancelled')),
      );
    } on MarketplaceApiException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not cancel — try again.')),
      );
    } finally {
      if (mounted) setState(() => _cancelling = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final sub = widget.sub;
    final name = sub.listing?.name ?? sub.listing?.slug ?? 'Subscription';
    final slug = sub.listing?.slug;
    final state = sub.subscriptionState;
    // Only still-renewing subscriptions can be cancelled.
    final canCancel = sub.autoRenew && (state == 'active' || state == 'grace');

    final String dateLine;
    switch (state) {
      case 'grace':
        dateLine = 'Grace until ${_fmtDate(sub.graceUntilDate)}';
        break;
      case 'expired':
        dateLine = 'Ended ${_fmtDate(sub.expiresAtDate)}';
        break;
      default:
        dateLine = sub.autoRenew
            ? 'Renews ${_fmtDate(sub.expiresAtDate)}'
            : 'Access until ${_fmtDate(sub.expiresAtDate)}';
    }

    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: slug == null ? null : () => context.push('/store/app/$slug'),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  CircleAvatar(
                    backgroundColor: cs.secondaryContainer,
                    child: Text(
                      name.isNotEmpty ? name[0].toUpperCase() : '?',
                      style: TextStyle(
                          color: cs.onSecondaryContainer,
                          fontWeight: FontWeight.w700),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style:
                                const TextStyle(fontWeight: FontWeight.w600)),
                        const SizedBox(height: 2),
                        Text(dateLine,
                            style:
                                TextStyle(color: cs.outline, fontSize: 12)),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  _StateChip(state: state),
                ],
              ),
              const SizedBox(height: 6),
              Row(
                children: [
                  Text(
                    '${formatAnm(sub.amountNanm)} ANM per renewal',
                    style: TextStyle(color: cs.outline, fontSize: 12),
                  ),
                  const Spacer(),
                  if (canCancel)
                    _cancelling
                        ? const Padding(
                            padding: EdgeInsets.symmetric(horizontal: 12),
                            child: SizedBox(
                              width: 18,
                              height: 18,
                              child:
                                  CircularProgressIndicator(strokeWidth: 2),
                            ),
                          )
                        : TextButton(
                            onPressed: _cancel,
                            child: const Text('Cancel'),
                          ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StateChip extends StatelessWidget {
  final String state;
  const _StateChip({required this.state});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final (label, bg, fg) = switch (state) {
      'active' => ('Active', cs.primaryContainer, cs.onPrimaryContainer),
      'grace' => ('Renewal failed', cs.errorContainer, cs.onErrorContainer),
      'expired' => (
          'Expired',
          cs.surfaceContainerHighest,
          cs.onSurfaceVariant
        ),
      _ => ('Cancelled', cs.surfaceContainerHighest, cs.onSurfaceVariant),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration:
          BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Text(label,
          style:
              TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 12)),
    );
  }
}

class _CustodialFootnote extends StatelessWidget {
  const _CustodialFootnote();
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
      child: Text(
        'Renewals debit your custodial Store balance (Animica can’t do '
        'non-custodial auto-pay). Top it up from your wallet; withdraw it '
        'on-chain anytime.',
        style: TextStyle(color: cs.outline, fontSize: 11, height: 1.35),
      ),
    );
  }
}

class _EmptySubs extends StatelessWidget {
  const _EmptySubs();
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      children: [
        const SizedBox(height: 90),
        Icon(Icons.autorenew, size: 56, color: cs.outline),
        const SizedBox(height: 12),
        Center(
          child: Text('No subscriptions',
              style: Theme.of(context).textTheme.titleMedium),
        ),
        const SizedBox(height: 6),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(
            'Subscriptions you start from the Store show up here, where you can '
            'see the next renewal and cancel anytime.',
            textAlign: TextAlign.center,
            style: TextStyle(color: cs.outline),
          ),
        ),
        const SizedBox(height: 16),
        Center(
          child: FilledButton.tonalIcon(
            icon: const Icon(Icons.storefront),
            label: const Text('Browse the Store'),
            onPressed: () => context.go('/store'),
          ),
        ),
      ],
    );
  }
}

class _SubsError extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _SubsError({required this.message, required this.onRetry});
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
