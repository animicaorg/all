// Subscribe consent sheet — the CUSTODIAL recurring-billing opt-in.
//
// Subscriptions on Animica are custodial by necessity: non-custodial pull
// payments are impossible, so renewals debit the buyer's in-app balance. This
// sheet is the honest consent surface. It:
//   1. States plainly that this is recurring + custodial, shows the price per
//      period, the 70/30 creator/marketplace split, and "cancel anytime".
//   2. On confirm, signs a single-use `subscribe` consent with the active
//      account (MarketplaceApi.subscribe → same ML-DSA-65 path as store login)
//      and creates the subscription server-side.
//   3. On 402 insufficient_funds, routes the user to Top up.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../state/store_state.dart';

/// Present the subscribe consent sheet for a [price] (SUBSCRIPTION model) on
/// [app]. No-op guarded by the caller (needs a signed-in account + a valid id).
Future<void> showStoreSubscribeSheet(
  BuildContext context, {
  required StoreAppDetail app,
  required StorePrice price,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (c) => _SubscribeSheet(app: app, price: price),
  );
}

/// Human label for a billing period, e.g. 30 → "month", 7 → "week", else "N days".
String periodLabel(int? days) {
  switch (days) {
    case 30:
    case 31:
      return 'month';
    case 7:
      return 'week';
    case 365:
    case 366:
      return 'year';
    case null:
      return 'period';
    default:
      return '$days days';
  }
}

enum _Phase { consent, working, success, insufficient, error }

class _SubscribeSheet extends ConsumerStatefulWidget {
  final StoreAppDetail app;
  final StorePrice price;
  const _SubscribeSheet({required this.app, required this.price});

  @override
  ConsumerState<_SubscribeSheet> createState() => _SubscribeSheetState();
}

class _SubscribeSheetState extends ConsumerState<_SubscribeSheet> {
  _Phase _phase = _Phase.consent;
  String? _error;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  void _set(VoidCallback fn) {
    if (!_disposed && mounted) setState(fn);
  }

  Future<void> _confirm() async {
    final priceId = widget.price.id;
    if (priceId == null || priceId.isEmpty) {
      _set(() {
        _phase = _Phase.error;
        _error = 'This subscription is misconfigured (no price id).';
      });
      return;
    }
    _set(() {
      _phase = _Phase.working;
      _error = null;
    });
    try {
      final api = ref.read(marketplaceApiProvider);
      // Bind the exact terms the server enforces into the signed consent
      // (periodDays defaults to the store's SUBSCRIPTION default of 30 when the
      // catalog omits it; a mismatch fails closed server-side as consent_mismatch).
      await api.subscribe(
        slug: widget.app.slug,
        priceId: priceId,
        amountNanm: widget.price.amountNanm,
        periodDays: widget.price.periodDays ?? 30,
      );
      // Freshly subscribed — refresh the dependent surfaces.
      ref.invalidate(mySubscriptionsProvider);
      ref.invalidate(myLicensesProvider);
      ref.invalidate(storeBalanceProvider);
      _set(() => _phase = _Phase.success);
    } on MarketplaceApiException catch (e) {
      if (e.isInsufficientFunds) {
        _set(() => _phase = _Phase.insufficient);
        return;
      }
      _set(() {
        _phase = _Phase.error;
        _error = e.isConflict
            ? 'You already have an active subscription for this app.'
            : e.message;
      });
    } catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = 'Could not subscribe: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 4,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.app.name,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 16),
          switch (_phase) {
            _Phase.consent => _ConsentView(
                price: widget.price,
                onConfirm: _confirm,
                onCancel: () => Navigator.of(context).pop(),
              ),
            _Phase.working => const _WorkingView(),
            _Phase.success => _SuccessView(
                price: widget.price,
                onDone: () => Navigator.of(context).pop(),
                onManage: () {
                  Navigator.of(context).pop();
                  context.push('/store/subscriptions');
                },
              ),
            _Phase.insufficient => _InsufficientView(
                onTopUp: () {
                  Navigator.of(context).pop();
                  context.push('/store/topup');
                },
                onClose: () => Navigator.of(context).pop(),
              ),
            _Phase.error => _ErrorView(
                message: _error ?? 'Something went wrong.',
                onRetry: () => _set(() => _phase = _Phase.consent),
                onClose: () => Navigator.of(context).pop(),
              ),
          },
        ],
      ),
    );
  }
}

class _ConsentView extends StatelessWidget {
  final StorePrice price;
  final VoidCallback onConfirm;
  final VoidCallback onCancel;
  const _ConsentView({
    required this.price,
    required this.onConfirm,
    required this.onCancel,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final per = periodLabel(price.periodDays);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text('Subscription',
                style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
            Text('${formatAnm(price.amountNanm)} ANM / $per',
                style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
          ],
        ),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: cs.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _Bullet(
                icon: Icons.autorenew,
                text: 'Auto-renews every $per until you cancel. We charge '
                    '${formatAnm(price.amountNanm)} ANM each $per.',
              ),
              const SizedBox(height: 10),
              const _Bullet(
                icon: Icons.account_balance_wallet_outlined,
                text: 'Custodial billing. Renewals debit your in-app Store '
                    'balance (Animica can’t do non-custodial auto-pay). Top it '
                    'up from your wallet; withdraw it on-chain anytime.',
              ),
              const SizedBox(height: 10),
              const _Bullet(
                icon: Icons.pie_chart_outline,
                text: 'Each payment splits 70% to the creator, 30% to the '
                    'marketplace.',
              ),
              const SizedBox(height: 10),
              const _Bullet(
                icon: Icons.event_busy_outlined,
                text: 'Cancel anytime. Access continues until the end of the '
                    'period you already paid for. No refunds for partial '
                    'periods.',
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        Text(
          'Tapping “Agree & subscribe” signs a one-time consent with your '
          'wallet key authorizing these recurring charges.',
          style: TextStyle(color: cs.outline, fontSize: 12, height: 1.35),
        ),
        const SizedBox(height: 18),
        FilledButton.icon(
          onPressed: onConfirm,
          icon: const Icon(Icons.verified_user),
          label: const Text('Agree & subscribe'),
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
        ),
        const SizedBox(height: 6),
        TextButton(onPressed: onCancel, child: const Text('Not now')),
      ],
    );
  }
}

class _Bullet extends StatelessWidget {
  final IconData icon;
  final String text;
  const _Bullet({required this.icon, required this.text});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 18, color: cs.primary),
        const SizedBox(width: 10),
        Expanded(
          child: Text(text,
              style: const TextStyle(fontSize: 13, height: 1.35)),
        ),
      ],
    );
  }
}

class _WorkingView extends StatelessWidget {
  const _WorkingView();
  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 28),
      child: Column(
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 18),
          Text('Signing consent & subscribing…', textAlign: TextAlign.center),
        ],
      ),
    );
  }
}

class _SuccessView extends StatelessWidget {
  final StorePrice price;
  final VoidCallback onDone;
  final VoidCallback onManage;
  const _SuccessView({
    required this.price,
    required this.onDone,
    required this.onManage,
  });
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final per = periodLabel(price.periodDays);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(height: 8),
        Icon(Icons.check_circle, size: 56, color: cs.primary),
        const SizedBox(height: 12),
        const Text('You’re subscribed',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 6),
        Text(
          'Renews every $per from your Store balance. Manage or cancel it '
          'anytime in Subscriptions.',
          textAlign: TextAlign.center,
          style: TextStyle(color: cs.outline),
        ),
        const SizedBox(height: 20),
        FilledButton(
            onPressed: onManage, child: const Text('Manage subscriptions')),
        const SizedBox(height: 8),
        TextButton(onPressed: onDone, child: const Text('Done')),
      ],
    );
  }
}

class _InsufficientView extends StatelessWidget {
  final VoidCallback onTopUp;
  final VoidCallback onClose;
  const _InsufficientView({required this.onTopUp, required this.onClose});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(height: 8),
        Icon(Icons.account_balance_wallet_outlined, size: 48, color: cs.primary),
        const SizedBox(height: 12),
        const Text('Not enough balance',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 6),
        Text(
          'Your Store balance can’t cover the first payment. Top up your '
          'balance, then subscribe.',
          textAlign: TextAlign.center,
          style: TextStyle(color: cs.outline),
        ),
        const SizedBox(height: 20),
        FilledButton.icon(
          onPressed: onTopUp,
          icon: const Icon(Icons.add),
          label: const Text('Top up balance'),
        ),
        const SizedBox(height: 8),
        TextButton(onPressed: onClose, child: const Text('Close')),
      ],
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  final VoidCallback onClose;
  const _ErrorView({
    required this.message,
    required this.onRetry,
    required this.onClose,
  });
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(height: 8),
        Icon(Icons.error_outline, size: 48, color: cs.error),
        const SizedBox(height: 12),
        Text(message, textAlign: TextAlign.center),
        const SizedBox(height: 20),
        FilledButton(onPressed: onRetry, child: const Text('Try again')),
        const SizedBox(height: 8),
        TextButton(onPressed: onClose, child: const Text('Close')),
      ],
    );
  }
}
