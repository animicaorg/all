// Checkout bottom sheet — the non-custodial App Store purchase flow.
//
// 1. POST /store/purchases/intent -> { payTo, amountNanm, memoHex, split{70/30} }.
// 2. Show the price + creator/marketplace split + pay-to; slide to confirm.
// 3. Build a kind=0 transfer whose value=amountNanm, to=payTo and data=memo bytes
//    (signer.dart buildTransferBody with the optional data param), sign locally,
//    broadcast via the failover RPC client.
// 4. POST /store/purchases/{id}/submit { txid }, then poll GET /store/purchases/{id}
//    through pending-payment -> confirming -> active, surfacing the issued License.
//
// The server NEVER trusts this txid to credit — the payment watcher verifies
// finality + a sender balance-delta execution proof — so a friendly "still
// confirming" outcome is normal; the purchase completes in My Apps regardless.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../services/signer.dart';
import '../../state/store_state.dart';
import '../../state/wallet_state.dart';

/// Present the checkout sheet for a paid ONE_TIME [price] on [app].
Future<void> showStoreCheckoutSheet(
  BuildContext context, {
  required StoreAppDetail app,
  required StorePrice price,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (c) => _CheckoutSheet(app: app, price: price),
  );
}

enum _Phase { intent, ready, working, success, error }

class _CheckoutSheet extends ConsumerStatefulWidget {
  final StoreAppDetail app;
  final StorePrice price;
  const _CheckoutSheet({required this.app, required this.price});

  @override
  ConsumerState<_CheckoutSheet> createState() => _CheckoutSheetState();
}

class _CheckoutSheetState extends ConsumerState<_CheckoutSheet> {
  _Phase _phase = _Phase.intent;
  PurchaseIntent? _intent;
  String? _error;
  String? _txHash;
  String _statusLine = '';
  License? _license;
  bool _disposed = false;

  @override
  void initState() {
    super.initState();
    _createIntent();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  void _set(VoidCallback fn) {
    if (!_disposed && mounted) setState(fn);
  }

  Future<void> _createIntent() async {
    _set(() {
      _phase = _Phase.intent;
      _error = null;
    });
    try {
      final api = ref.read(marketplaceApiProvider);
      final intent = await api.createPurchaseIntent(
        slug: widget.app.slug,
        priceId: widget.price.id,
      );
      _set(() {
        _intent = intent;
        _phase = _Phase.ready;
      });
    } on MarketplaceApiException catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = (e.isConflict && e.code == 'already_owned')
            ? 'You already own this app — find it in My Apps.'
            : (e.isUnavailable
                ? 'The App Store is not accepting purchases yet.'
                : e.message);
      });
    } catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = 'Could not start checkout: $e';
      });
    }
  }

  Future<void> _confirmAndPay() async {
    final intent = _intent;
    final account = ref.read(activeAccountProvider);
    if (intent == null || account == null) return;

    final memo = intent.memoBytes;
    if (memo.isEmpty) {
      _set(() {
        _phase = _Phase.error;
        _error = 'Malformed payment memo — please try again.';
      });
      return;
    }

    _set(() {
      _phase = _Phase.working;
      _statusLine = 'Signing payment…';
      _error = null;
    });

    final api = ref.read(marketplaceApiProvider);
    final rpc = ref.read(rpcProvider);
    try {
      final nonce = await rpc.getPendingNonce(account.address);
      final chainId = await rpc.chainId();
      final body = buildTransferBody(
        from: account.address,
        to: intent.payTo,
        amountNanos: intent.amountNanm,
        nonce: nonce,
        chainId: chainId,
        data: Uint8List.fromList(memo),
      );
      _set(() => _statusLine = 'Broadcasting to the network…');
      final txHash = await signAndBroadcast(rpc: rpc, account: account, body: body);
      _set(() => _txHash = txHash);

      _set(() => _statusLine = 'Recording payment…');
      await api.submitPurchaseTxid(
        purchaseId: intent.purchaseId,
        txid: txHash,
      );

      await _poll(api, intent.purchaseId);
    } on RpcError catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = 'Broadcast failed: ${e.message}';
      });
    } on MarketplaceApiException catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = e.message;
      });
    } catch (e) {
      _set(() {
        _phase = _Phase.error;
        _error = 'Payment failed: $e';
      });
    }
  }

  /// Poll the purchase through confirming -> active. Bounded (~5 min); a
  /// still-confirming purchase is not an error — it finishes in My Apps.
  Future<void> _poll(MarketplaceApi api, String purchaseId) async {
    _set(() => _statusLine = 'Waiting for confirmations…');
    for (var i = 0; i < 60; i++) {
      if (_disposed) return;
      await Future<void>.delayed(const Duration(seconds: 5));
      if (_disposed) return;
      PurchaseStatusResult res;
      try {
        res = await api.purchaseStatus(purchaseId);
      } on MarketplaceApiException {
        continue; // transient — keep polling
      }
      final fail = res.intent?.failReason;
      if (fail != null && fail.isNotEmpty) {
        _set(() {
          _phase = _Phase.error;
          _error = 'Payment rejected: $fail';
        });
        return;
      }
      if (res.purchase.isActive || res.license != null) {
        _set(() {
          _phase = _Phase.success;
          _license = res.license;
        });
        // Freshly-owned — the library should reflect it.
        ref.invalidate(myLicensesProvider);
        return;
      }
      if (res.intent?.expired ?? false) {
        _set(() {
          _phase = _Phase.error;
          _error = 'The payment window expired before it confirmed.';
        });
        return;
      }
      final confirming = (res.intent?.submittedTxid ?? '').isNotEmpty;
      _set(() => _statusLine = confirming
          ? 'Confirming on-chain (12 confirmations)…'
          : 'Waiting for the payment to be seen…');
    }
    // Timed out waiting — leave it to finish in the background.
    _set(() {
      _phase = _Phase.success;
      _statusLine = 'stillConfirming';
    });
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
            _Phase.intent =>
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 40),
                child: Center(child: CircularProgressIndicator()),
              ),
            _Phase.ready => _ReadyView(
                intent: _intent!,
                onConfirm: _confirmAndPay,
              ),
            _Phase.working => _WorkingView(status: _statusLine, txHash: _txHash),
            _Phase.success => _SuccessView(
                license: _license,
                stillConfirming: _statusLine == 'stillConfirming',
                onDone: () => Navigator.of(context).pop(),
                onOpenLibrary: () {
                  Navigator.of(context).pop();
                  context.push('/store/library');
                },
              ),
            _Phase.error => _ErrorView(
                message: _error ?? 'Something went wrong.',
                onRetry: _intent == null ? _createIntent : null,
                onClose: () => Navigator.of(context).pop(),
              ),
          },
        ],
      ),
    );
  }
}

class _ReadyView extends StatelessWidget {
  final PurchaseIntent intent;
  final VoidCallback onConfirm;
  const _ReadyView({required this.intent, required this.onConfirm});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final split = intent.split;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        _AmountLine(
          label: 'Total',
          value: '${formatAnm(intent.amountNanm)} ANM',
          emphasized: true,
        ),
        if (split != null) ...[
          const SizedBox(height: 8),
          _AmountLine(
            label: 'Creator (${_pct(10000 - split.feeBps)})',
            value: '${formatAnm(split.creatorNanm)} ANM',
          ),
          _AmountLine(
            label: 'Marketplace (${_pct(split.feeBps)})',
            value: '${formatAnm(split.treasuryNanm)} ANM',
          ),
        ],
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: cs.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Pay to',
                  style: TextStyle(color: cs.outline, fontSize: 12)),
              const SizedBox(height: 2),
              SelectableText(intent.payTo,
                  style: const TextStyle(
                      fontFamily: 'monospace', fontSize: 12)),
              const SizedBox(height: 6),
              Text(
                'A single on-chain payment. The marketplace splits it to the '
                'creator; you can verify your license on-chain afterwards.',
                style: TextStyle(color: cs.outline, fontSize: 11, height: 1.3),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        _SlideToConfirm(label: 'Slide to pay', onConfirmed: onConfirm),
        const SizedBox(height: 8),
      ],
    );
  }

  static String _pct(int bps) => '${(bps / 100).toStringAsFixed(0)}%';
}

class _AmountLine extends StatelessWidget {
  final String label;
  final String value;
  final bool emphasized;
  const _AmountLine({
    required this.label,
    required this.value,
    this.emphasized = false,
  });
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final style = TextStyle(
      fontWeight: emphasized ? FontWeight.w700 : FontWeight.w500,
      fontSize: emphasized ? 16 : 13,
      color: emphasized ? null : cs.onSurfaceVariant,
    );
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [Text(label, style: style), Text(value, style: style)],
      ),
    );
  }
}

class _WorkingView extends StatelessWidget {
  final String status;
  final String? txHash;
  const _WorkingView({required this.status, required this.txHash});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(
        children: [
          const CircularProgressIndicator(),
          const SizedBox(height: 20),
          Text(status, textAlign: TextAlign.center),
          if (txHash != null) ...[
            const SizedBox(height: 12),
            Text('Tx ${_short(txHash!)}',
                style: TextStyle(
                    fontFamily: 'monospace', fontSize: 11, color: cs.outline)),
          ],
          const SizedBox(height: 8),
          Text('Keep this open — it only takes a moment.',
              style: TextStyle(color: cs.outline, fontSize: 12)),
        ],
      ),
    );
  }

  static String _short(String h) {
    final s = h.startsWith('0x') ? h.substring(2) : h;
    if (s.length <= 16) return h;
    return '${s.substring(0, 8)}…${s.substring(s.length - 6)}';
  }
}

class _SuccessView extends StatelessWidget {
  final License? license;
  final bool stillConfirming;
  final VoidCallback onDone;
  final VoidCallback onOpenLibrary;
  const _SuccessView({
    required this.license,
    required this.stillConfirming,
    required this.onDone,
    required this.onOpenLibrary,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(height: 8),
        Icon(stillConfirming ? Icons.schedule : Icons.check_circle,
            size: 56, color: cs.primary),
        const SizedBox(height: 12),
        Text(
          stillConfirming ? 'Payment sent' : 'Purchase complete',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 6),
        Text(
          stillConfirming
              ? 'Your payment is confirming on-chain. Your license will appear '
                  'in My Apps once it finalizes.'
              : 'Your license has been issued.',
          textAlign: TextAlign.center,
          style: TextStyle(color: cs.outline),
        ),
        if (license != null && !stillConfirming) ...[
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: cs.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('License',
                    style: TextStyle(color: cs.outline, fontSize: 12)),
                const SizedBox(height: 2),
                SelectableText(license!.id,
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 12)),
                if (license!.kind != null) ...[
                  const SizedBox(height: 4),
                  Text(license!.kind!.toLowerCase()),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 20),
        FilledButton(onPressed: onOpenLibrary, child: const Text('Open My Apps')),
        const SizedBox(height: 8),
        TextButton(onPressed: onDone, child: const Text('Done')),
      ],
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;
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
        if (onRetry != null)
          FilledButton(onPressed: onRetry, child: const Text('Try again')),
        const SizedBox(height: 8),
        TextButton(onPressed: onClose, child: const Text('Close')),
      ],
    );
  }
}

/// A drag-to-confirm control: the user drags the thumb to the far right to fire
/// [onConfirmed] once. Keeps a destructive/irreversible action (spending ANM)
/// behind a deliberate gesture, matching the checkout "slide-to-confirm" spec.
class _SlideToConfirm extends StatefulWidget {
  final String label;
  final VoidCallback onConfirmed;
  const _SlideToConfirm({required this.label, required this.onConfirmed});
  @override
  State<_SlideToConfirm> createState() => _SlideToConfirmState();
}

class _SlideToConfirmState extends State<_SlideToConfirm> {
  static const double _height = 56;
  static const double _thumb = 52;
  double _dx = 0;
  bool _fired = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxTravel = (constraints.maxWidth - _thumb - 4).clamp(0.0, 4000.0);
        final progress = maxTravel == 0 ? 0.0 : (_dx / maxTravel);
        return Container(
          height: _height,
          decoration: BoxDecoration(
            color: cs.primaryContainer,
            borderRadius: BorderRadius.circular(_height / 2),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              Opacity(
                opacity: (1 - progress).clamp(0.0, 1.0),
                child: Text(
                  widget.label,
                  style: TextStyle(
                    color: cs.onPrimaryContainer,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              Positioned(
                left: 2 + _dx,
                child: GestureDetector(
                  onHorizontalDragUpdate: (d) {
                    if (_fired) return;
                    setState(() => _dx = (_dx + d.delta.dx).clamp(0.0, maxTravel));
                  },
                  onHorizontalDragEnd: (_) {
                    if (_fired) return;
                    if (_dx >= maxTravel * 0.9) {
                      setState(() {
                        _dx = maxTravel;
                        _fired = true;
                      });
                      HapticFeedback.mediumImpact();
                      widget.onConfirmed();
                    } else {
                      setState(() => _dx = 0);
                    }
                  },
                  child: Container(
                    width: _thumb,
                    height: _thumb,
                    decoration: BoxDecoration(
                      color: cs.primary,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      _fired ? Icons.check : Icons.chevron_right,
                      color: cs.onPrimary,
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
