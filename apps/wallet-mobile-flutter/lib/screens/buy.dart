// Buy ANM with PayPal.
//
// Flow: pick a USD amount → POST /api/onramp/paypal/orders (locks a quote,
// returns a PayPal approval link + the exact ANM you'll receive) → open the
// approval link in the browser → on return we capture the payment and poll
// the order until the ANM lands in this wallet.
//
// The desk (buy-sol PayPal rail, proxied at animica.dev/api/onramp) is
// DORMANT until the operator arms it. 404/503 on order-create degrade to a
// friendly "almost ready" card rather than an error. The old NOWPayments
// gateway (buy.animica.org) currently serves 503, so it is no longer linked.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import '../constants.dart';
import '../services/rpc.dart';
import '../state/wallet_state.dart';

/// Preset USD amounts offered on the picker.
const List<int> _presets = [25, 50, 100, 500];

/// Where the buyer is in the checkout.
enum _Stage {
  amount, // picking how much to buy
  creating, // POSTing the order + quote
  awaitingApproval, // order created, waiting for the buyer to approve in PayPal
  processing, // captured / delivering — polling for the ANM
  complete, // ANM delivered
  failed, // order failed / needs support attention
  expired, // the locked quote expired before payment
  dormant, // the rail isn't armed yet (404/503)
}

class BuyScreen extends ConsumerStatefulWidget {
  const BuyScreen({super.key});
  @override
  ConsumerState<BuyScreen> createState() => _BuyScreenState();
}

class _BuyScreenState extends ConsumerState<BuyScreen>
    with WidgetsBindingObserver {
  final _api = _OnrampApi();
  final _custom = TextEditingController();

  _Stage _stage = _Stage.amount;
  int? _preset; // highlighted preset ($), null when a custom amount is typed
  String? _error; // inline error on the amount card

  // Active order.
  String? _orderId;
  String? _approveUrl;
  String? _expectedAnm;
  DateTime? _quoteExpiresAt;
  Map<String, dynamic>? _order; // latest polled public order view
  String? _captureHint; // transient "finish checkout" hint on the wait card

  bool _polling = false;
  bool _slow = false; // delivery is taking longer than usual

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _custom.dispose();
    _api.close();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // The buyer just came back from the PayPal browser tab. Capture the
    // payment while they're watching, then start polling for delivery.
    if (state == AppLifecycleState.resumed &&
        _stage == _Stage.awaitingApproval &&
        !_polling) {
      _captureAndPoll();
    }
  }

  String? get _address => ref.read(activeAccountProvider)?.address;

  /// The USD amount the buyer has chosen (preset or a valid custom entry).
  double? get _usd {
    final t = _custom.text.trim();
    if (t.isNotEmpty) return double.tryParse(t);
    if (_preset != null) return _preset!.toDouble();
    return null;
  }

  void _reset() {
    setState(() {
      _stage = _Stage.amount;
      _error = null;
      _orderId = null;
      _approveUrl = null;
      _expectedAnm = null;
      _quoteExpiresAt = null;
      _order = null;
      _captureHint = null;
      _slow = false;
    });
  }

  Future<void> _createOrder() async {
    final addr = _address;
    if (addr == null) {
      setState(() => _error = 'No active account. Create one first.');
      return;
    }
    final usd = _usd;
    if (usd == null || usd <= 0) {
      setState(() => _error = 'Enter an amount to buy.');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      _stage = _Stage.creating;
    });
    final res = await _api.createOrder(usd: usd, address: addr);
    if (!mounted) return;
    switch (res.outcome) {
      case _CreateOutcome.ok:
        final o = res.order!;
        setState(() {
          _orderId = o['orderId'] as String?;
          _approveUrl = o['approveUrl'] as String?;
          _expectedAnm = o['expectedAnm'] as String?;
          _quoteExpiresAt = _parseTs(o['quoteExpiresAt']);
          _stage = _Stage.awaitingApproval;
          _captureHint = null;
        });
        await _openApprove();
        break;
      case _CreateOutcome.dormant:
        setState(() => _stage = _Stage.dormant);
        break;
      case _CreateOutcome.error:
        setState(() {
          _stage = _Stage.amount;
          _error = res.message ?? 'Could not start your purchase.';
        });
        break;
    }
  }

  Future<void> _openApprove() async {
    final url = _approveUrl;
    if (url == null) return;
    final uri = Uri.parse(url);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  /// Capture the payment (buyer returned from PayPal), then poll for delivery.
  Future<void> _captureAndPoll() async {
    final addr = _address;
    final id = _orderId;
    if (addr == null || id == null) return;
    setState(() => _captureHint = null);
    final res = await _api.captureReturn(id: id, address: addr);
    if (!mounted) return;
    switch (res.outcome) {
      case _CaptureOutcome.ok:
        _applyOrder(res.order!);
        if (_stage == _Stage.awaitingApproval || _stage == _Stage.processing) {
          setState(() => _stage = _Stage.processing);
          _pollLoop();
        }
        break;
      case _CaptureOutcome.notApproved:
        // The buyer hasn't finished the PayPal checkout yet.
        setState(() => _captureHint =
            'We haven\'t seen your PayPal approval yet. Finish the checkout, then tap "I\'ve paid".');
        break;
      case _CaptureOutcome.expired:
        setState(() => _stage = _Stage.expired);
        break;
      case _CaptureOutcome.dormant:
        setState(() => _stage = _Stage.dormant);
        break;
      case _CaptureOutcome.error:
        setState(() => _captureHint =
            res.message ?? 'Could not confirm your payment. Try again in a moment.');
        break;
    }
  }

  Future<void> _pollLoop() async {
    if (_polling) return;
    final addr = _address;
    final id = _orderId;
    if (addr == null || id == null) return;
    _polling = true;
    if (mounted) setState(() => _slow = false);
    final deadline = DateTime.now().add(const Duration(minutes: 5));
    try {
      while (mounted && _stage == _Stage.processing) {
        final order = await _api.getOrder(id: id, address: addr);
        if (order != null && mounted) {
          _applyOrder(order);
          final status = order['status'] as String?;
          if (status == 'COMPLETE') {
            setState(() => _stage = _Stage.complete);
            ref.invalidate(balanceProvider);
            return;
          }
          if (status == 'FAILED' ||
              status == 'REFUND_NEEDED' ||
              status == 'MANUAL_REVIEW') {
            setState(() => _stage = _Stage.failed);
            return;
          }
        }
        if (DateTime.now().isAfter(deadline)) {
          if (mounted) setState(() => _slow = true);
          return;
        }
        await Future.delayed(const Duration(seconds: 3));
      }
    } finally {
      _polling = false;
    }
  }

  void _applyOrder(Map<String, dynamic> order) {
    setState(() {
      _order = order;
      final anm = order['expectedAnm'];
      if (anm is String) _expectedAnm = anm;
      _quoteExpiresAt = _parseTs(order['quoteExpiresAt']) ?? _quoteExpiresAt;
    });
  }

  @override
  Widget build(BuildContext context) {
    final acc = ref.watch(activeAccountProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Buy ANM')),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: acc == null
              ? const _NoAccount()
              : switch (_stage) {
                  _Stage.amount => _buildAmount(context, acc.address),
                  _Stage.creating => const _Busy(label: 'Locking in your rate…'),
                  _Stage.awaitingApproval => _buildAwaiting(context),
                  _Stage.processing => _buildProcessing(context),
                  _Stage.complete => _buildComplete(context),
                  _Stage.failed => _buildFailed(context),
                  _Stage.expired => _buildExpired(context),
                  _Stage.dormant => _buildDormant(context),
                },
        ),
      ),
    );
  }

  // ── amount picker ──────────────────────────────────────────────────
  Widget _buildAmount(BuildContext context, String address) {
    final theme = Theme.of(context);
    final canContinue = (_usd ?? 0) > 0;
    return ListView(
      children: [
        Row(children: [
          Icon(Icons.account_balance_wallet_outlined,
              color: theme.colorScheme.primary),
          const SizedBox(width: 8),
          const Expanded(
            child: Text('Buy ANM with PayPal',
                style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
          ),
        ]),
        const SizedBox(height: 6),
        Text(
          'Pay in USD with PayPal and the ANM is delivered straight to this wallet.',
          style: TextStyle(color: theme.colorScheme.outline, fontSize: 13),
        ),
        const SizedBox(height: 20),
        Text('Amount',
            style: theme.textTheme.labelMedium
                ?.copyWith(color: theme.colorScheme.outline)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            for (final p in _presets)
              _PresetChip(
                label: '\$$p',
                selected: _preset == p && _custom.text.trim().isEmpty,
                onTap: () => setState(() {
                  _preset = p;
                  _custom.clear();
                  _error = null;
                }),
              ),
          ],
        ),
        const SizedBox(height: 14),
        TextField(
          controller: _custom,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          onChanged: (_) => setState(() {
            _preset = null;
            _error = null;
          }),
          decoration: const InputDecoration(
            labelText: 'Custom amount',
            prefixText: '\$ ',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 10),
        Row(children: [
          Icon(Icons.info_outline, size: 15, color: theme.colorScheme.outline),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              'You\'ll see the exact ANM amount and locked rate before you pay.',
              style: TextStyle(color: theme.colorScheme.outline, fontSize: 12),
            ),
          ),
        ]),
        const SizedBox(height: 16),
        Text('Delivery address',
            style: theme.textTheme.labelMedium
                ?.copyWith(color: theme.colorScheme.outline)),
        const SizedBox(height: 4),
        SelectableText(address,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
        if (_error != null) ...[
          const SizedBox(height: 16),
          _ErrorBox(_error!),
        ],
        const SizedBox(height: 24),
        FilledButton(
          onPressed: canContinue ? _createOrder : null,
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: const Text('Continue to PayPal'),
        ),
      ],
    );
  }

  // ── awaiting PayPal approval ───────────────────────────────────────
  Widget _buildAwaiting(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      children: [
        Card(
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Icon(Icons.open_in_new, color: theme.colorScheme.primary),
                  const SizedBox(width: 8),
                  const Expanded(
                    child: Text('Complete your payment in PayPal',
                        style: TextStyle(fontWeight: FontWeight.w700)),
                  ),
                ]),
                const SizedBox(height: 10),
                if (_expectedAnm != null)
                  _KeyValue(label: 'You\'ll receive', value: '$_expectedAnm ANM'),
                if (_quoteExpiresAt != null) ...[
                  const SizedBox(height: 6),
                  _KeyValue(
                    label: 'Rate locked until',
                    value: TimeOfDay.fromDateTime(_quoteExpiresAt!.toLocal())
                        .format(context),
                  ),
                ],
                const SizedBox(height: 12),
                Text(
                  'A PayPal checkout opened in your browser. After you approve the payment, come back here — we\'ll finish delivery automatically.',
                  style:
                      TextStyle(color: theme.colorScheme.outline, fontSize: 13),
                ),
              ],
            ),
          ),
        ),
        if (_captureHint != null) ...[
          const SizedBox(height: 16),
          _ErrorBox(_captureHint!),
        ],
        const SizedBox(height: 24),
        FilledButton.icon(
          icon: const Icon(Icons.check_circle_outline),
          label: const Text('I\'ve paid — finish delivery'),
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          onPressed: _captureAndPoll,
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          icon: const Icon(Icons.open_in_new),
          label: const Text('Reopen PayPal checkout'),
          onPressed: _openApprove,
        ),
        const SizedBox(height: 6),
        TextButton(onPressed: _reset, child: const Text('Cancel')),
      ],
    );
  }

  // ── processing / delivery ──────────────────────────────────────────
  Widget _buildProcessing(BuildContext context) {
    final theme = Theme.of(context);
    final status = _order?['status'] as String?;
    final txid = _deliveredTxid;
    return ListView(
      children: [
        Row(children: [
          const SizedBox(
              height: 18,
              width: 18,
              child: CircularProgressIndicator(strokeWidth: 2)),
          const SizedBox(width: 10),
          Text('Delivering your ANM',
              style: theme.textTheme.titleMedium
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ]),
        const SizedBox(height: 20),
        _ProgressSteps(status: status, txid: txid),
        if (_slow) ...[
          const SizedBox(height: 20),
          const _ErrorBox(
              'This is taking longer than usual. Your payment is safe — you can keep waiting or check again.'),
          const SizedBox(height: 12),
          OutlinedButton.icon(
            icon: const Icon(Icons.refresh),
            label: const Text('Check again'),
            onPressed: _pollLoop,
          ),
        ],
      ],
    );
  }

  // ── complete ───────────────────────────────────────────────────────
  Widget _buildComplete(BuildContext context) {
    final theme = Theme.of(context);
    final txid = _deliveredTxid;
    final anm = _expectedAnm ?? _deliveredAnm;
    return ListView(
      children: [
        const SizedBox(height: 8),
        Center(
          child: Icon(Icons.check_circle,
              size: 64, color: theme.colorScheme.primary),
        ),
        const SizedBox(height: 12),
        Center(
          child: Text('ANM delivered',
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ),
        const SizedBox(height: 6),
        Center(
          child: Text(
            anm != null ? '+$anm ANM is now in your wallet' : 'Your ANM is in your wallet',
            style: TextStyle(color: theme.colorScheme.outline),
          ),
        ),
        const SizedBox(height: 24),
        if (txid != null) ...[
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: theme.colorScheme.primaryContainer,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Delivery transaction',
                    style: TextStyle(
                        color: theme.colorScheme.onPrimaryContainer,
                        fontWeight: FontWeight.w700)),
                const SizedBox(height: 4),
                SelectableText(txid,
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 10)),
                const SizedBox(height: 8),
                TextButton.icon(
                  icon: const Icon(Icons.open_in_new, size: 16),
                  label: const Text('View on explorer'),
                  onPressed: () => launchUrl(
                    Uri.parse(AnimicaConfig.explorerTxUrl
                        .replaceAll('{txHash}', txid)),
                    mode: LaunchMode.externalApplication,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
        ],
        FilledButton(
          onPressed: () => context.pop(),
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: const Text('Done'),
        ),
        const SizedBox(height: 8),
        TextButton(onPressed: _reset, child: const Text('Buy more')),
      ],
    );
  }

  // ── failed / review ────────────────────────────────────────────────
  Widget _buildFailed(BuildContext context) {
    final theme = Theme.of(context);
    final status = _order?['status'] as String?;
    final reason = _order?['failedReason'] as String?;
    final review = status == 'REFUND_NEEDED' || status == 'MANUAL_REVIEW';
    return ListView(
      children: [
        const SizedBox(height: 8),
        Center(
          child: Icon(review ? Icons.hourglass_top : Icons.error_outline,
              size: 60, color: theme.colorScheme.error),
        ),
        const SizedBox(height: 12),
        Center(
          child: Text(review ? 'Under review' : 'Purchase didn\'t complete',
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ),
        const SizedBox(height: 10),
        Text(
          review
              ? 'Your payment is being reviewed by the desk. If any charge was taken it will be refunded — no action is needed from you right now.'
              : (reason ??
                  'This order didn\'t complete. If PayPal charged you, it will be refunded automatically.'),
          textAlign: TextAlign.center,
          style: TextStyle(color: theme.colorScheme.outline),
        ),
        const SizedBox(height: 24),
        FilledButton(
          onPressed: _reset,
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: const Text('Start over'),
        ),
      ],
    );
  }

  // ── quote expired ──────────────────────────────────────────────────
  Widget _buildExpired(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      children: [
        const SizedBox(height: 8),
        Center(
          child: Icon(Icons.timer_off_outlined,
              size: 60, color: theme.colorScheme.outline),
        ),
        const SizedBox(height: 12),
        Center(
          child: Text('Quote expired',
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ),
        const SizedBox(height: 10),
        Text(
          'The locked rate expired before payment, so nothing was charged. Start a new order to get a fresh quote.',
          textAlign: TextAlign.center,
          style: TextStyle(color: theme.colorScheme.outline),
        ),
        const SizedBox(height: 24),
        FilledButton(
          onPressed: _reset,
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: const Text('New order'),
        ),
      ],
    );
  }

  // ── rail not armed yet ─────────────────────────────────────────────
  Widget _buildDormant(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      children: [
        const SizedBox(height: 8),
        Center(
          child: Icon(Icons.rocket_launch_outlined,
              size: 60, color: theme.colorScheme.primary),
        ),
        const SizedBox(height: 12),
        Center(
          child: Text('Buy with PayPal is almost ready',
              textAlign: TextAlign.center,
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ),
        const SizedBox(height: 10),
        Text(
          'The PayPal on-ramp is being switched on. Check back soon — you\'ll be able to buy ANM with PayPal right here in the wallet.',
          textAlign: TextAlign.center,
          style: TextStyle(color: theme.colorScheme.outline),
        ),
        const SizedBox(height: 24),
        OutlinedButton.icon(
          icon: const Icon(Icons.refresh),
          label: const Text('Try again'),
          onPressed: _reset,
        ),
      ],
    );
  }

  // ── derived helpers ────────────────────────────────────────────────
  String? get _deliveredTxid {
    final anm = _order?['anm'];
    if (anm is Map && anm['deliveredTxid'] is String) {
      return anm['deliveredTxid'] as String;
    }
    return null;
  }

  String? get _deliveredAnm {
    final anm = _order?['anm'];
    if (anm is Map && anm['deliveredBase'] != null) {
      final base = BigInt.tryParse(anm['deliveredBase'].toString());
      if (base != null) return formatAnm(base);
    }
    return null;
  }
}

DateTime? _parseTs(dynamic v) => v is String ? DateTime.tryParse(v) : null;

// ── small shared widgets ──────────────────────────────────────────────

class _PresetChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;
  const _PresetChip(
      {required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ChoiceChip(
      label: Text(label),
      selected: selected,
      onSelected: (_) => onTap(),
      labelStyle: TextStyle(
        fontWeight: FontWeight.w600,
        color: selected ? cs.onSecondaryContainer : cs.onSurface,
      ),
      showCheckmark: false,
      shape:
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    );
  }
}

class _KeyValue extends StatelessWidget {
  final String label;
  final String value;
  const _KeyValue({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: TextStyle(color: cs.outline, fontSize: 13)),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w700)),
      ],
    );
  }
}

class _ErrorBox extends StatelessWidget {
  final String message;
  const _ErrorBox(this.message);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.errorContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(message, style: TextStyle(color: cs.onErrorContainer)),
    );
  }
}

class _Busy extends StatelessWidget {
  final String label;
  const _Busy({required this.label});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const CircularProgressIndicator(),
          const SizedBox(height: 16),
          Text(label, style: TextStyle(color: Theme.of(context).colorScheme.outline)),
        ],
      ),
    );
  }
}

class _NoAccount extends StatelessWidget {
  const _NoAccount();
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Text('No active account.',
          style: TextStyle(color: Theme.of(context).colorScheme.outline)),
    );
  }
}

/// A tiny vertical stepper reflecting the order status:
/// AWAITING_PAYMENT → PAID → DELIVERING → SENT → COMPLETE.
class _ProgressSteps extends StatelessWidget {
  final String? status;
  final String? txid;
  const _ProgressSteps({required this.status, required this.txid});

  static int _rank(String? s) {
    switch (s) {
      case 'PAID':
        return 1;
      case 'DELIVERING':
        return 2;
      case 'SENT':
        return 3;
      case 'COMPLETE':
        return 4;
      default:
        return 0; // AWAITING_PAYMENT / unknown
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _rank(status);
    return Column(
      children: [
        _StepRow(label: 'Payment confirmed', done: r >= 1, active: r == 0),
        _StepRow(label: 'Preparing delivery', done: r >= 2, active: r == 1),
        _StepRow(
          label: 'ANM sent to your wallet',
          done: r >= 3,
          active: r == 2,
          subtitle: (r >= 3 && txid != null)
              ? '${txid!.substring(0, 10)}…${txid!.substring(txid!.length - 6)}'
              : null,
        ),
        _StepRow(label: 'Confirmed on chain', done: r >= 4, active: r == 3),
      ],
    );
  }
}

class _StepRow extends StatelessWidget {
  final String label;
  final bool done;
  final bool active;
  final String? subtitle;
  const _StepRow({
    required this.label,
    required this.done,
    required this.active,
    this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final Widget leading;
    if (done) {
      leading = Icon(Icons.check_circle, color: cs.primary, size: 22);
    } else if (active) {
      leading = const SizedBox(
        height: 20,
        width: 20,
        child: Padding(
          padding: EdgeInsets.all(1),
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    } else {
      leading = Icon(Icons.circle_outlined, color: cs.outline, size: 22);
    }
    final faded = !done && !active;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          leading,
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: TextStyle(
                    fontWeight: active ? FontWeight.w700 : FontWeight.w500,
                    color: faded ? cs.outline : cs.onSurface,
                  ),
                ),
                if (subtitle != null)
                  Text(subtitle!,
                      style: const TextStyle(
                          fontFamily: 'monospace', fontSize: 11)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ── on-ramp REST client ───────────────────────────────────────────────
// Talks to the buy-sol PayPal desk proxied at animica.dev/api/onramp
// (nginx strips /api → Fastify /onramp/...). Kept local to this screen so
// the shared rpc/vault services stay untouched.

enum _CreateOutcome { ok, dormant, error }

class _CreateResult {
  final _CreateOutcome outcome;
  final Map<String, dynamic>? order;
  final String? message;
  const _CreateResult(this.outcome, {this.order, this.message});
}

enum _CaptureOutcome { ok, notApproved, expired, dormant, error }

class _CaptureResult {
  final _CaptureOutcome outcome;
  final Map<String, dynamic>? order;
  final String? message;
  const _CaptureResult(this.outcome, {this.order, this.message});
}

class _OnrampApi {
  final http.Client _http = http.Client();
  static const _base = AnimicaConfig.onrampApiUrl;
  static const _timeout = Duration(seconds: 15);

  Future<_CreateResult> createOrder({
    required double usd,
    required String address,
  }) async {
    try {
      final resp = await _http
          .post(
            Uri.parse('$_base/paypal/orders'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({
              'usdAmount': _usdString(usd),
              'animicaAddress': address,
            }),
          )
          .timeout(_timeout);
      final body = _decode(resp.body);
      if (resp.statusCode == 200) {
        final order = body is Map ? body['order'] : null;
        if (order is Map<String, dynamic>) {
          return _CreateResult(_CreateOutcome.ok, order: order);
        }
        return const _CreateResult(_CreateOutcome.error,
            message: 'Unexpected response from the desk.');
      }
      // Rail not armed / paused / float exhausted → "almost ready".
      if (resp.statusCode == 404 || resp.statusCode == 503) {
        return const _CreateResult(_CreateOutcome.dormant);
      }
      return _CreateResult(_CreateOutcome.error,
          message: _errMsg(body, 'Could not start your purchase.'));
    } catch (_) {
      // Can't reach the desk at all — treat as not-yet-available.
      return const _CreateResult(_CreateOutcome.dormant);
    }
  }

  Future<_CaptureResult> captureReturn({
    required String id,
    required String address,
  }) async {
    try {
      final resp = await _http
          .post(
            Uri.parse('$_base/paypal/orders/$id/capture-return'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'animicaAddress': address}),
          )
          .timeout(_timeout);
      final body = _decode(resp.body);
      switch (resp.statusCode) {
        case 200:
          final order = body is Map ? body['order'] : null;
          if (order is Map<String, dynamic>) {
            return _CaptureResult(_CaptureOutcome.ok, order: order);
          }
          return const _CaptureResult(_CaptureOutcome.error,
              message: 'Unexpected response from the desk.');
        case 409:
          return const _CaptureResult(_CaptureOutcome.notApproved);
        case 410:
          return const _CaptureResult(_CaptureOutcome.expired);
        case 503:
          return const _CaptureResult(_CaptureOutcome.dormant);
        default:
          return _CaptureResult(_CaptureOutcome.error,
              message: _errMsg(body, 'Could not confirm your payment.'));
      }
    } catch (_) {
      return const _CaptureResult(_CaptureOutcome.error,
          message: 'Network error confirming your payment. Try again.');
    }
  }

  /// Poll the public order view. Returns null on any non-200 (transient /
  /// not-found) — the caller keeps polling against its own deadline.
  Future<Map<String, dynamic>?> getOrder({
    required String id,
    required String address,
  }) async {
    try {
      final uri = Uri.parse('$_base/paypal/orders/$id')
          .replace(queryParameters: {'address': address});
      final resp = await _http.get(uri).timeout(_timeout);
      if (resp.statusCode != 200) return null;
      final body = _decode(resp.body);
      final order = body is Map ? body['order'] : null;
      return order is Map<String, dynamic> ? order : null;
    } catch (_) {
      return null;
    }
  }

  void close() => _http.close();

  static dynamic _decode(String body) {
    try {
      return jsonDecode(body);
    } catch (_) {
      return null;
    }
  }

  /// PayPal wants at most 2 dp; drop a trailing `.0` for whole dollars.
  static String _usdString(double usd) {
    final s = usd.toStringAsFixed(2);
    return s.endsWith('.00') ? s.substring(0, s.length - 3) : s;
  }

  static String _errMsg(dynamic body, String fallback) {
    if (body is Map && body['error'] is String) return body['error'] as String;
    return fallback;
  }
}
