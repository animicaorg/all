// Promote (feature) a token on the DEX discover page. This is a plain
// treasury transfer with a typed promo memo — it works today, independent of
// on-chain contract execution. An indexer scanning foundation-treasury inbound
// transfers reconstructs the featured set from these deposits.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../services/price.dart';
import '../../services/rpc.dart';
import '../../state/dex_state.dart';
import '../../state/wallet_state.dart';
import 'dex_common.dart';

class PromoteScreen extends ConsumerStatefulWidget {
  final String contract;
  const PromoteScreen({super.key, required this.contract});

  @override
  ConsumerState<PromoteScreen> createState() => _PromoteScreenState();
}

class _PromoteScreenState extends ConsumerState<PromoteScreen> {
  final _label = TextEditingController();
  int _days = 1;
  bool _busy = false;
  String? _submittedTx;

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  String? _usdOf(BigInt nanos) {
    final market = ref.read(anmMarketProvider).value;
    if (market == null) return null;
    return formatUsd(usdValueOfNanos(nanos, market.usdPerAnm));
  }

  Future<void> _promote() async {
    final days = _days;
    final total = DexService.promoTotalNanos(days);
    final usd = _usdOf(total);

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Confirm promotion'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Feature this token for $days × 24h.'),
            const SizedBox(height: 12),
            Text(
              '${formatAnm(total)} ANM${usd != null ? '  ·  ≈ $usd' : ''}',
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
            const SizedBox(height: 8),
            const Text(
              'Sent to the Animica foundation treasury. Promotions are not '
              'refundable once the transfer settles.',
              style: TextStyle(fontSize: 12.5),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Pay & promote'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    if (!mounted) return;

    setState(() => _busy = true);
    try {
      final outcome = await ref.read(dexActionsProvider).promote(
            contract: widget.contract,
            days: days,
            label: _label.text.trim(),
          );
      if (!mounted) return;
      showTxResult(context, outcome);
      setState(() => _submittedTx = outcome.txHash);
      // Best-effort balance refresh once the transfer has had time to land.
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted) ref.invalidate(balanceProvider);
      });
    } catch (e) {
      if (!mounted) return;
      showTxError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final account = ref.watch(activeAccountProvider);
    final token = ref.watch(tokenInfoProvider(widget.contract)).value;
    final balance = ref.watch(balanceProvider).value;
    final market = ref.watch(anmMarketProvider).value;

    final total = DexService.promoTotalNanos(_days);
    final perDay = DexService.promoTotalNanos(1);
    final totalUsd =
        market == null ? null : formatUsd(usdValueOfNanos(total, market.usdPerAnm));
    final insufficient = balance != null && balance < total;

    return Scaffold(
      appBar: AppBar(title: const Text('Promote token')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: ListView(
          children: [
            const DexCapabilityBanner(transferStillWorks: true),

            // What is being promoted.
            if (token != null) ...[
              Text('${token.symbol} — ${token.name}',
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
              const SizedBox(height: 2),
            ],
            Text(widget.contract,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
            if (token != null && token.promoted) ...[
              const SizedBox(height: 8),
              Text(
                token.promoDaysLeft != null
                    ? 'Currently featured — ${token.promoDaysLeft} day(s) left. '
                        'New periods extend the run.'
                    : 'Currently featured. New periods extend the run.',
                style: TextStyle(color: cs.tertiary, fontSize: 12.5),
              ),
            ],
            const SizedBox(height: 20),

            // Duration picker.
            Text('Duration',
                style: theme.textTheme.labelMedium?.copyWith(color: cs.outline)),
            Row(
              children: [
                IconButton(
                  icon: const Icon(Icons.remove_circle_outline),
                  onPressed: _busy || _days <= 1
                      ? null
                      : () => setState(() => _days -= 1),
                ),
                Expanded(
                  child: Slider(
                    value: _days.toDouble(),
                    min: 1,
                    max: PromoConfig.maxDays.toDouble(),
                    divisions: PromoConfig.maxDays > 1 ? PromoConfig.maxDays - 1 : null,
                    label: '$_days × 24h',
                    onChanged:
                        _busy ? null : (v) => setState(() => _days = v.round()),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.add_circle_outline),
                  onPressed: _busy || _days >= PromoConfig.maxDays
                      ? null
                      : () => setState(() => _days += 1),
                ),
              ],
            ),
            Center(
              child: Text('$_days × 24h',
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18)),
            ),
            const SizedBox(height: 12),

            // Cost breakdown.
            StatRow(
              label: 'Fee per 24h',
              value: '${formatAnm(perDay)} ANM',
              sub: _usdOf(perDay),
            ),
            StatRow(
              label: 'Total',
              value: '${formatAnm(total)} ANM',
              sub: totalUsd,
            ),
            if (balance != null)
              StatRow(
                label: 'Your balance',
                value: '${formatAnm(balance)} ANM',
                valueColor: insufficient ? cs.error : null,
                sub: insufficient ? 'Not enough to cover the total' : null,
              ),
            const SizedBox(height: 12),

            TextField(
              controller: _label,
              enabled: !_busy,
              maxLength: 48,
              decoration: const InputDecoration(
                labelText: 'Label (optional)',
                hintText: 'Shown in the feature card',
                border: OutlineInputBorder(),
                counterText: '',
              ),
            ),
            const SizedBox(height: 16),

            // How it works — honest, on-chain mechanics.
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: cs.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Featured for $_days × 24h. Paid to the Animica foundation '
                    'treasury. An indexer features your token from this '
                    'on-chain deposit.',
                    style: const TextStyle(fontSize: 12.5),
                  ),
                  const SizedBox(height: 8),
                  Text('Treasury',
                      style: TextStyle(color: cs.outline, fontSize: 11)),
                  const SelectableText(
                    PromoConfig.treasuryAddress,
                    style: TextStyle(fontFamily: 'monospace', fontSize: 10.5),
                  ),
                ],
              ),
            ),

            if (_submittedTx != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: cs.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('✓ Promotion submitted',
                        style: TextStyle(
                            color: cs.onPrimaryContainer,
                            fontWeight: FontWeight.w700)),
                    const SizedBox(height: 4),
                    SelectableText(_submittedTx!,
                        style: const TextStyle(
                            fontFamily: 'monospace', fontSize: 10)),
                    const SizedBox(height: 4),
                    Text(
                      'The feature card appears once the indexer picks up the '
                      'deposit.',
                      style:
                          TextStyle(color: cs.onPrimaryContainer, fontSize: 12),
                    ),
                  ],
                ),
              ),
            ],

            if (account == null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: cs.errorContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text('No active account. Create or select a wallet first.',
                    style: TextStyle(color: cs.onErrorContainer)),
              ),
            ],

            const SizedBox(height: 20),
            FilledButton(
              onPressed: _busy || account == null ? null : _promote,
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              child: _busy
                  ? const SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : Text('Promote — ${formatAnm(total)} ANM'),
            ),
            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }
}
