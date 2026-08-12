// ANM Instant (L2) — Animica 10.0.0 ANM-native rollup.
//
// Shows the account's ANM Instant balance as a DISTINCT balance of the same
// asset (never silently merged with, or moved from, L1 ANM), plus the four
// L2 flows: Send-Instant, Deposit (L1→L2 via the bridge), Withdraw-to-L1, and
// a live lifecycle status chip (SOFT_CONFIRMED → PROVEN → L1_FINALIZED).
//
// Every send runs the wallet signing recipe from services/l2.dart:
// prepare → (user confirms echoed recipient/amount/fee) → sign the node's
// 64-byte signingHash with the account's EXISTING ML-DSA-65 key → submit →
// poll. It reuses the active L1 account unchanged — the L2 address == the L1
// address. Additive: routed from home, touches no L1 path.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../constants.dart';
import '../models/account.dart';
import '../services/address.dart';
import '../services/l2.dart';
import '../services/rpc.dart';
import '../state/wallet_state.dart';

/// L2 client bound to the shared RPC client (same endpoint as L1).
final l2ClientProvider = Provider<L2Client>((ref) {
  return L2Client(ref.watch(rpcProvider));
});

/// Node's L2 status: enabled flag, mode, head batch, bridge, sig backend.
final l2StatusProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) {
  return ref.watch(l2ClientProvider).l2Status();
});

/// ANM Instant balance of the active account (distinct from L1 balance).
final l2BalanceProvider = FutureProvider.autoDispose<L2Balance?>((ref) async {
  final acc = ref.watch(activeAccountProvider);
  if (acc == null) return null;
  return ref.watch(l2ClientProvider).l2GetBalance(acc.address);
});

/// Live L2 throughput, refreshed while the screen is open.
final l2TpsProvider = FutureProvider.autoDispose<double>((ref) {
  final t = Timer(const Duration(seconds: 15), ref.invalidateSelf);
  ref.onDispose(t.cancel);
  return ref.watch(l2ClientProvider).l2GetTPS();
});

class L2Screen extends ConsumerWidget {
  const L2Screen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeAccountProvider);
    final status = ref.watch(l2StatusProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('ANM Instant'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              ref.invalidate(l2StatusProvider);
              ref.invalidate(l2BalanceProvider);
              ref.invalidate(l2TpsProvider);
            },
          ),
        ],
      ),
      body: active == null
          ? const Center(child: Text('No active account.'))
          : status.when(
              loading: () =>
                  const Center(child: CircularProgressIndicator()),
              error: (e, _) => _StatusError(error: e),
              data: (s) {
                final enabled = s['enabled'] == true;
                if (!enabled && s.isNotEmpty) {
                  return const _L2Disabled();
                }
                return RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(l2StatusProvider);
                    await ref.refresh(l2BalanceProvider.future);
                  },
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      _L2BalanceCard(),
                      const SizedBox(height: 16),
                      _NetworkChips(status: s),
                      const SizedBox(height: 16),
                      _ActionCard(
                        icon: Icons.bolt,
                        title: 'Send Instant',
                        subtitle: 'Near-instant ANM transfer on L2',
                        onTap: () => _showSend(context, ref, active),
                      ),
                      _ActionCard(
                        icon: Icons.south_west,
                        title: 'Deposit from ANM (L1)',
                        subtitle: 'Move ANM onto Instant via the bridge',
                        onTap: () => _showDeposit(context, ref, s, active),
                      ),
                      _ActionCard(
                        icon: Icons.north_east,
                        title: 'Withdraw to ANM (L1)',
                        subtitle: 'Exit Instant back to your L1 balance',
                        onTap: () => _showWithdraw(context, ref, active),
                      ),
                    ],
                  ),
                );
              },
            ),
    );
  }

  void _showSend(BuildContext context, WidgetRef ref, Account active) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.of(context).viewInsets.bottom),
        child: _TransferSheet(account: active, kind: L2Kind.transfer),
      ),
    );
  }

  void _showWithdraw(BuildContext context, WidgetRef ref, Account active) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.of(context).viewInsets.bottom),
        child: _TransferSheet(account: active, kind: L2Kind.withdraw),
      ),
    );
  }

  void _showDeposit(BuildContext context, WidgetRef ref,
      Map<String, dynamic> status, Account active) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _DepositSheet(status: status),
    );
  }
}

// ── balance + network ───────────────────────────────────────────────────

class _L2BalanceCard extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cs = Theme.of(context).colorScheme;
    final bal = ref.watch(l2BalanceProvider);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [cs.tertiary, cs.tertiary.withOpacity(0.7)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.bolt, size: 16, color: cs.onTertiary),
              const SizedBox(width: 4),
              Text('ANM Instant balance',
                  style: TextStyle(
                      color: cs.onTertiary.withOpacity(0.9), fontSize: 13)),
            ],
          ),
          const SizedBox(height: 6),
          bal.when(
            loading: () => Text('— ANM',
                style: TextStyle(color: cs.onTertiary, fontSize: 28)),
            error: (_, __) => Text('?',
                style: TextStyle(color: cs.onTertiary, fontSize: 28)),
            data: (b) => RichText(
              text: TextSpan(
                style: TextStyle(
                    color: cs.onTertiary,
                    fontSize: 28,
                    fontWeight: FontWeight.w600),
                children: [
                  TextSpan(text: formatAnm(b?.balance ?? BigInt.zero)),
                  const TextSpan(
                      text: ' ANM',
                      style: TextStyle(
                          fontSize: 16, fontWeight: FontWeight.normal)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 4),
          Text('Separate from your L1 ANM — same asset, faster layer.',
              style: TextStyle(
                  color: cs.onTertiary.withOpacity(0.75), fontSize: 11)),
        ],
      ),
    );
  }
}

class _NetworkChips extends ConsumerWidget {
  final Map<String, dynamic> status;
  const _NetworkChips({required this.status});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tps = ref.watch(l2TpsProvider).value;
    final chips = <Widget>[
      if (status['mode'] != null)
        _chip(context, Icons.tune, 'Mode', status['mode'].toString()),
      if (status['l2ChainId'] != null)
        _chip(context, Icons.tag, 'Chain', status['l2ChainId'].toString()),
      if (status['headBatch'] != null)
        _chip(context, Icons.layers, 'Head batch',
            status['headBatch'].toString()),
      if (status['settlementMode'] != null)
        _chip(context, Icons.verified, 'Settlement',
            status['settlementMode'].toString()),
      if (tps != null)
        _chip(context, Icons.speed, 'TPS', tps.toStringAsFixed(1)),
    ];
    if (chips.isEmpty) return const SizedBox.shrink();
    return Wrap(spacing: 8, runSpacing: 8, children: chips);
  }

  Widget _chip(
      BuildContext context, IconData icon, String label, String value) {
    return Chip(
      avatar: Icon(icon, size: 16),
      label: Text('$label: $value', style: const TextStyle(fontSize: 12)),
      visualDensity: VisualDensity.compact,
    );
  }
}

class _ActionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  const _ActionCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: ListTile(
        leading: Icon(icon),
        title: Text(title,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(subtitle),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}

// ── send / withdraw sheet (shared) ──────────────────────────────────────

class _TransferSheet extends ConsumerStatefulWidget {
  final Account account;
  final L2Kind kind;
  const _TransferSheet({required this.account, required this.kind});

  @override
  ConsumerState<_TransferSheet> createState() => _TransferSheetState();
}

class _TransferSheetState extends ConsumerState<_TransferSheet> {
  final _to = TextEditingController();
  final _amount = TextEditingController();
  String? _err;
  bool _busy = false;
  L2TxStatus? _status;
  String? _txid;

  bool get _isWithdraw => widget.kind == L2Kind.withdraw;

  @override
  void initState() {
    super.initState();
    if (_isWithdraw) {
      // Withdrawals return to the same account's L1 address by default.
      _to.text = widget.account.address;
    }
  }

  @override
  void dispose() {
    _to.dispose();
    _amount.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _err = null;
      _status = null;
      _txid = null;
    });
    final to = _to.text.trim();
    if (!isValidAnimAddress(to)) {
      setState(() => _err = _isWithdraw
          ? 'Invalid L1 payout address.'
          : 'Invalid recipient address.');
      return;
    }
    final BigInt amountNanos;
    try {
      amountNanos = _anmToNanos(_amount.text.trim());
    } catch (_) {
      setState(() => _err = 'Amount must be a number with at most 9 decimals.');
      return;
    }
    if (amountNanos <= BigInt.zero) {
      setState(() => _err = 'Amount must be greater than zero.');
      return;
    }

    setState(() => _busy = true);
    final l2 = ref.read(l2ClientProvider);
    try {
      final future = _isWithdraw
          ? l2.withdrawToL1(
              account: widget.account,
              amountNanos: amountNanos,
              toL1Address: to,
              confirm: _confirm,
              onStatus: _onStatus,
            )
          : l2.sendInstant(
              account: widget.account,
              to: to,
              amountNanos: amountNanos,
              confirm: _confirm,
              onStatus: _onStatus,
            );
      final res = await future;
      if (res == null) {
        // User declined at the confirm step.
        if (mounted) setState(() => _busy = false);
        return;
      }
      if (mounted) {
        setState(() {
          _txid = res.txid;
          _status = res.status;
        });
        ref.invalidate(l2BalanceProvider);
        if (res.status.isFailure) {
          setState(() => _err =
              'The network ${res.status.wire.toLowerCase()} this transfer'
              '${res.reason != null ? ' (${res.reason})' : ''}. '
              'Your ANM Instant balance is unchanged.');
        }
      }
    } on RpcError catch (e) {
      if (mounted) setState(() => _err = e.message);
    } catch (e) {
      if (mounted) setState(() => _err = 'Failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<bool> _confirm(L2Prepared p) async {
    if (!mounted) return false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(_isWithdraw ? 'Confirm withdrawal' : 'Confirm Instant send'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _kv('To', _short(p.recipient)),
            _kv('Amount', '${formatAnm(p.amount, maxDecimals: 9)} ANM'),
            _kv('Fee', '${formatAnm(p.fee, maxDecimals: 9)} ANM'),
            _kv('Nonce', p.nonce.toString()),
            const SizedBox(height: 8),
            Text('These values are echoed by the node and are exactly what '
                'it will settle.',
                style: Theme.of(ctx).textTheme.bodySmall),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Sign & send')),
        ],
      ),
    );
    return ok ?? false;
  }

  void _onStatus(L2TxStatus s) {
    if (mounted) setState(() => _status = s);
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(k, style: const TextStyle(fontWeight: FontWeight.w600)),
            Flexible(
                child: Text(v,
                    textAlign: TextAlign.right,
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 12))),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(_isWithdraw ? 'Withdraw to ANM (L1)' : 'Send Instant',
                style: theme.textTheme.titleLarge),
            const SizedBox(height: 12),
            TextField(
              controller: _to,
              decoration: InputDecoration(
                labelText: _isWithdraw ? 'L1 payout address' : 'To address',
                hintText: 'anim1…',
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _amount,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: 'Amount',
                border: OutlineInputBorder(),
                suffixText: 'ANM',
              ),
            ),
            if (_status != null) ...[
              const SizedBox(height: 12),
              L2StatusChip(status: _status!),
            ],
            if (_txid != null) ...[
              const SizedBox(height: 8),
              SelectableText(_txid!,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 10)),
            ],
            if (_err != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: theme.colorScheme.errorContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(_err!,
                    style:
                        TextStyle(color: theme.colorScheme.onErrorContainer)),
              ),
            ],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _busy ? null : _submit,
                style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14)),
                child: _busy
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(_isWithdraw ? 'Withdraw' : 'Send Instant'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _short(String a) => a.length > 20
      ? '${a.substring(0, 10)}…${a.substring(a.length - 8)}'
      : a;

  BigInt _anmToNanos(String s) {
    final cleaned = s.trim();
    final dot = cleaned.indexOf('.');
    if (dot < 0) {
      return BigInt.parse(cleaned) * AnimicaConfig.nanosPerAnm;
    }
    final whole = cleaned.substring(0, dot);
    var frac = cleaned.substring(dot + 1);
    if (frac.length > 9) throw const FormatException('too many decimals');
    frac = frac.padRight(9, '0');
    return BigInt.parse(whole.isEmpty ? '0' : whole) *
            AnimicaConfig.nanosPerAnm +
        BigInt.parse(frac);
  }
}

// ── deposit sheet ─────────────────────────────────────────────────────────

class _DepositSheet extends StatelessWidget {
  final Map<String, dynamic> status;
  const _DepositSheet({required this.status});

  String? get _bridgeAddress {
    final b = status['bridge'];
    if (b is Map) {
      for (final k in const [
        'depositAddress',
        'deposit_address',
        'address',
        'l1Address',
        'l1_address',
      ]) {
        final v = b[k];
        if (v is String && v.isNotEmpty) return v;
      }
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final addr = _bridgeAddress;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Deposit to ANM Instant', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              'Send an ordinary ANM (L1) transfer to the bridge address below. '
              'After the L1 transfer finalizes, the node credits the same '
              'amount to your ANM Instant balance automatically.',
              style: theme.textTheme.bodyMedium,
            ),
            const SizedBox(height: 16),
            if (addr == null)
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                    'The node did not report a bridge deposit address. '
                    'Deposits may be disabled on this network.'),
              )
            else ...[
              Text('Bridge deposit address',
                  style: theme.textTheme.labelMedium
                      ?.copyWith(color: theme.colorScheme.outline)),
              const SizedBox(height: 4),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: SelectableText(addr,
                          style: const TextStyle(
                              fontFamily: 'monospace', fontSize: 12)),
                    ),
                    IconButton(
                      icon: const Icon(Icons.copy, size: 18),
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: addr));
                        if (!context.mounted) return;
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                              content: Text('Bridge address copied'),
                              duration: Duration(seconds: 1)),
                        );
                      },
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              Text(
                'Only send ANM (L1) to this address. The credit appears on '
                'ANM Instant after L1 finality, not immediately.',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.outline),
              ),
            ],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Done'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ── lifecycle status chip ───────────────────────────────────────────────

/// Maps the L2 lifecycle to a color + label. Crucially, SOFT_CONFIRMED is
/// shown as "sequencer accepted" — an interim state — and is NEVER presented
/// as settlement; only PROVEN/L1_FINALIZED are settled.
class L2StatusChip extends StatelessWidget {
  final L2TxStatus status;
  const L2StatusChip({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    late final Color bg;
    late final Color fg;
    late final IconData icon;
    late final String label;
    switch (status) {
      case L2TxStatus.received:
      case L2TxStatus.validated:
        bg = cs.surfaceContainerHighest;
        fg = cs.onSurface;
        icon = Icons.hourglass_empty;
        label = 'Submitted — validating';
        break;
      case L2TxStatus.softConfirmed:
        bg = cs.secondaryContainer;
        fg = cs.onSecondaryContainer;
        icon = Icons.flash_on;
        label = 'Sequencer accepted (not yet settled)';
        break;
      case L2TxStatus.batched:
        bg = cs.secondaryContainer;
        fg = cs.onSecondaryContainer;
        icon = Icons.layers;
        label = 'Batched — proving';
        break;
      case L2TxStatus.proven:
        bg = cs.primaryContainer;
        fg = cs.onPrimaryContainer;
        icon = Icons.verified;
        label = 'Proven on L2';
        break;
      case L2TxStatus.l1Submitted:
        bg = cs.primaryContainer;
        fg = cs.onPrimaryContainer;
        icon = Icons.upload;
        label = 'Proven — settling on L1';
        break;
      case L2TxStatus.l1Finalized:
        bg = cs.primaryContainer;
        fg = cs.onPrimaryContainer;
        icon = Icons.check_circle;
        label = 'Finalized on L1';
        break;
      case L2TxStatus.failed:
      case L2TxStatus.reverted:
        bg = cs.errorContainer;
        fg = cs.onErrorContainer;
        icon = Icons.error_outline;
        label = status == L2TxStatus.reverted ? 'Reverted' : 'Failed';
        break;
      case L2TxStatus.unknown:
        bg = cs.surfaceContainerHighest;
        fg = cs.onSurface;
        icon = Icons.help_outline;
        label = 'Pending';
        break;
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration:
          BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: fg),
          const SizedBox(width: 6),
          Flexible(
            child: Text(label,
                style: TextStyle(color: fg, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }
}

// ── empty / error states ────────────────────────────────────────────────

class _L2Disabled extends StatelessWidget {
  const _L2Disabled();
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.bolt_outlined,
                size: 56, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 12),
            Text('ANM Instant is not enabled',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(
              'The connected node does not have the L2 rollup turned on. Your '
              'ANM (L1) balance and transfers are unaffected.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Theme.of(context).colorScheme.outline),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusError extends StatelessWidget {
  final Object error;
  const _StatusError({required this.error});
  @override
  Widget build(BuildContext context) {
    // A node that predates 10.0.0 answers l2_status with "method not found".
    final s = error.toString().toLowerCase();
    final unsupported =
        s.contains('method not found') || s.contains('-32601');
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(unsupported ? Icons.bolt_outlined : Icons.cloud_off,
                size: 56, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 12),
            Text(
              unsupported
                  ? 'This node does not support ANM Instant'
                  : 'Could not reach ANM Instant',
              style: Theme.of(context).textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              unsupported
                  ? 'The L2 rollup was added in Animica 10.0.0. Your L1 wallet '
                      'works normally.'
                  : '$error',
              textAlign: TextAlign.center,
              style: TextStyle(color: Theme.of(context).colorScheme.outline),
            ),
          ],
        ),
      ),
    );
  }
}
