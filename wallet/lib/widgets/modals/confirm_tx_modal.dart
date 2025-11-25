import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../buttons/primary_button.dart';

/// Simple model describing a transaction preview for confirmation.
class ConfirmTxModel {
  final String from;                 // sender address (display only)
  final String to;                   // recipient address
  final String amountUnits;          // minimal units as decimal-string
  final String feeUnits;             // minimal units as decimal-string (network fee)
  final int decimals;                // token decimals (usually 18)
  final String symbol;               // token symbol (e.g., ANM)
  final String? memo;                // optional memo / note
  final String? dataHex;             // optional hex-encoded data/payload
  final int? chainId;                // network chain id
  final String? networkName;         // human label for network
  final int? nonce;                  // optional account nonce
  final int? gasLimit;               // optional gas / weight units

  const ConfirmTxModel({
    required this.from,
    required this.to,
    required this.amountUnits,
    required this.feeUnits,
    required this.decimals,
    required this.symbol,
    this.memo,
    this.dataHex,
    this.chainId,
    this.networkName,
    this.nonce,
    this.gasLimit,
  });
}

/// Result returned by [showConfirmTxModal] if the user confirmed.
class ConfirmTxResult {
  /// Whether the user explicitly confirmed (true) or canceled (false / null).
  final bool confirmed;

  /// Optional tx hash returned by the caller after sending.
  final String? txHash;

  /// Optional error message set if sending failed inside the modal.
  final String? error;

  const ConfirmTxResult.confirmed({this.txHash})
      : confirmed = true,
        error = null;

  const ConfirmTxResult.canceled()
      : confirmed = false,
        txHash = null,
        error = null;

  const ConfirmTxResult.failed(this.error)
      : confirmed = false,
        txHash = null;
}

/// Show a modal bottom sheet with the transaction details and Confirm/Cancel buttons.
///
/// If you pass [onSend], the modal will run it when the user taps "Confirm",
/// show a loading state, and close automatically on success (returning
/// [ConfirmTxResult.confirmed(txHash: ...)]). If [onSend] throws, the error
/// message is shown inline and the modal stays open.
///
/// If you omit [onSend], the modal simply returns confirmed/canceled.
Future<ConfirmTxResult?> showConfirmTxModal(
  BuildContext context, {
  required ConfirmTxModel tx,
  Future<String> Function()? onSend, // returns tx hash on success
}) {
  return showModalBottomSheet<ConfirmTxResult>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: Theme.of(context).colorScheme.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (ctx) => _ConfirmTxSheet(tx: tx, onSend: onSend),
  );
}

class _ConfirmTxSheet extends StatefulWidget {
  const _ConfirmTxSheet({required this.tx, required this.onSend});
  final ConfirmTxModel tx;
  final Future<String> Function()? onSend;

  @override
  State<_ConfirmTxSheet> createState() => _ConfirmTxSheetState();
}

class _ConfirmTxSheetState extends State<_ConfirmTxSheet> {
  bool _expanded = false;
  bool _sending = false;
  String? _error;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final media = MediaQuery.of(context);

    final prettyAmount = _prettyUnits(widget.tx.amountUnits, widget.tx.decimals);
    final prettyFee = _prettyUnits(widget.tx.feeUnits, widget.tx.decimals);
    final totalUnits =
        _safeAddUnits(widget.tx.amountUnits, widget.tx.feeUnits);
    final prettyTotal = _prettyUnits(totalUnits, widget.tx.decimals);

    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 12,
        // Make space for keyboard if any
        bottom: media.viewInsets.bottom + 16,
      ),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Grab handle
            Container(
              width: 44,
              height: 5,
              margin: const EdgeInsets.only(bottom: 12),
              decoration: BoxDecoration(
                color: cs.outlineVariant,
                borderRadius: BorderRadius.circular(3),
              ),
            ),

            Row(
              children: [
                Expanded(
                  child: Text(
                    'Confirm Transaction',
                    style: theme.textTheme.titleLarge,
                  ),
                ),
                if (widget.tx.networkName != null)
                  _NetworkChip(
                    label: widget.tx.networkName!,
                    chainId: widget.tx.chainId,
                  ),
              ],
            ),

            const SizedBox(height: 12),

            _KV('From', _shortAddr(widget.tx.from), copyValue: widget.tx.from),
            _KV('To', _shortAddr(widget.tx.to), copyValue: widget.tx.to),
            _KV('Amount', '$prettyAmount ${widget.tx.symbol}'),
            _KV('Network fee', '$prettyFee ${widget.tx.symbol}'),
            _KV('Total', '$prettyTotal ${widget.tx.symbol}',
                valueStyle:
                    theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),

            if (widget.tx.memo != null && widget.tx.memo!.isNotEmpty) ...[
              const SizedBox(height: 8),
              _KV('Memo', widget.tx.memo!),
            ],

            const SizedBox(height: 8),

            AnimatedCrossFade(
              firstChild: _AdvancedCollapsed(onTap: () {
                setState(() => _expanded = true);
              }),
              secondChild: _AdvancedExpanded(
                tx: widget.tx,
                dataHex: widget.tx.dataHex,
                onCollapse: () => setState(() => _expanded = false),
              ),
              crossFadeState:
                  _expanded ? CrossFadeState.showSecond : CrossFadeState.showFirst,
              duration: const Duration(milliseconds: 180),
            ),

            if (_error != null) ...[
              const SizedBox(height: 8),
              _ErrorBanner(message: _error!),
            ],

            const SizedBox(height: 16),

            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: _sending
                        ? null
                        : () {
                            Navigator.of(context).pop(const ConfirmTxResult.canceled());
                          },
                    child: const Text('Cancel'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: PrimaryButton(
                    label: _sending ? 'Sending…' : 'Confirm & Send',
                    loading: _sending,
                    onPressed: _sending ? null : _onConfirmPressed,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
          ],
        ),
      ),
    );
  }

  Future<void> _onConfirmPressed() async {
    // No sender provided → just close with confirmed
    if (widget.onSend == null) {
      if (!mounted) return;
      Navigator.of(context).pop(const ConfirmTxResult.confirmed());
      return;
    }
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final hash = await widget.onSend!.call();
      if (!mounted) return;
      Navigator.of(context).pop(ConfirmTxResult.confirmed(txHash: hash));
    } catch (e) {
      setState(() {
        _sending = false;
        _error = e.toString();
      });
    }
  }
}

// ------------------------ Widgets ------------------------

class _KV extends StatelessWidget {
  const _KV(this.k, this.v, {this.valueStyle, this.copyValue});
  final String k;
  final String v;
  final TextStyle? valueStyle;
  final String? copyValue;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final row = Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 120,
          child: Text(k, style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.textTheme.bodyMedium?.color?.withOpacity(0.72),
          )),
        ),
        Expanded(
          child: SelectableText(
            v,
            style: valueStyle ?? theme.textTheme.bodyLarge,
          ),
        ),
        if (copyValue != null)
          IconButton(
            tooltip: 'Copy',
            icon: const Icon(Icons.copy, size: 18),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: copyValue!));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Copied')),
              );
            },
          ),
      ],
    );
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: row,
    );
  }
}

class _NetworkChip extends StatelessWidget {
  const _NetworkChip({required this.label, this.chainId});
  final String label;
  final int? chainId;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final txt = chainId != null ? '$label · chainId ${chainId!}' : label;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: cs.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
      ),
      child: Text(
        txt,
        style: TextStyle(color: cs.onSecondaryContainer),
      ),
    );
  }
}

class _AdvancedCollapsed extends StatelessWidget {
  const _AdvancedCollapsed({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: cs.surfaceVariant.withOpacity(0.5),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
        ),
        child: Row(
          children: [
            const Icon(Icons.tune, size: 18),
            const SizedBox(width: 8),
            Text(
              'Advanced',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const Spacer(),
            const Icon(Icons.expand_more),
          ],
        ),
      ),
    );
  }
}

class _AdvancedExpanded extends StatelessWidget {
  const _AdvancedExpanded({
    required this.tx,
    required this.dataHex,
    required this.onCollapse,
  });

  final ConfirmTxModel tx;
  final String? dataHex;
  final VoidCallback onCollapse;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: cs.surfaceVariant.withOpacity(0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
      ),
      child: Column(
        children: [
          Row(
            children: [
              const Icon(Icons.tune, size: 18),
              const SizedBox(width: 8),
              Text('Advanced', style: theme.textTheme.bodyMedium),
              const Spacer(),
              IconButton(
                tooltip: 'Collapse',
                icon: const Icon(Icons.expand_less),
                onPressed: onCollapse,
              ),
            ],
          ),
          const SizedBox(height: 8),
          _KV('Nonce', tx.nonce?.toString() ?? '—'),
          _KV('Gas / Weight', tx.gasLimit?.toString() ?? '—'),
          if (dataHex != null && dataHex!.isNotEmpty) ...[
            const SizedBox(height: 6),
            _KV(
              'Data (hex)',
              _shortHex(dataHex!, 64),
              copyValue: dataHex,
              valueStyle: theme.textTheme.bodySmall?.copyWith(
                fontFamily: 'monospace',
              ),
            ),
          ],
          const SizedBox(height: 4),
          _SecurityHints(address: tx.to),
        ],
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.errorContainer,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: cs.error.withOpacity(0.4)),
      ),
      child: Row(
        children: [
          Icon(Icons.error_outline, color: cs.onErrorContainer),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: TextStyle(color: cs.onErrorContainer),
            ),
          ),
        ],
      ),
    );
  }
}

class _SecurityHints extends StatelessWidget {
  const _SecurityHints({required this.address});
  final String address;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.surface.withOpacity(0.6),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.shield_outlined, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Security tip: verify the recipient address matches your intended destination.\n'
              'Never share your seed phrase. Transactions are irreversible.',
              style: theme.textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------ helpers ------------------------

String _shortAddr(String addr) {
  if (addr.length <= 18) return addr;
  if (addr.startsWith('0x') || addr.startsWith('0X')) {
    return '${addr.substring(0, 10)}…${addr.substring(addr.length - 8)}';
  }
  return '${addr.substring(0, 8)}…${addr.substring(addr.length - 6)}';
}

String _shortHex(String hex, int keep) {
  final t = hex.trim();
  if (t.length <= keep) return t;
  return '${t.substring(0, keep)}…';
}

String _safeAddUnits(String a, String b) {
  // Minimal string-based adder for non-negative integers.
  String x = a.replaceFirst(RegExp(r'^0+'), '');
  String y = b.replaceFirst(RegExp(r'^0+'), '');
  if (x.isEmpty) x = '0';
  if (y.isEmpty) y = '0';
  final nx = x.length, ny = y.length;
  final n = math.max(nx, ny);
  int carry = 0;
  final buf = StringBuffer();
  for (int i = 0; i < n; i++) {
    final dx = i < nx ? (x.codeUnitAt(nx - 1 - i) - 48) : 0;
    final dy = i < ny ? (y.codeUnitAt(ny - 1 - i) - 48) : 0;
    int s = dx + dy + carry;
    carry = s ~/ 10;
    s = s % 10;
    buf.writeCharCode(48 + s);
  }
  if (carry > 0) buf.writeCharCode(48 + carry);
  final rev = buf.toString().split('').reversed.join();
  return rev.replaceFirst(RegExp(r'^0+'), '').isEmpty ? '0' : rev.replaceFirst(RegExp(r'^0+'), '');
}

String _prettyUnits(String units, int decimals, {int maxFractionDigits = 6}) {
  String u = units.trim();
  bool neg = u.startsWith('-');
  if (neg) u = u.substring(1);
  u = u.replaceFirst(RegExp(r'^0+'), '');
  if (u.isEmpty) u = '0';

  if (decimals <= 0) return neg ? '-$u' : u;

  if (u.length <= decimals) {
    final frac = u.padLeft(decimals, '0');
    final limited = frac.substring(0, math.min(frac.length, maxFractionDigits));
    final trimmed = limited.replaceFirst(RegExp(r'0+$'), '');
    final out = '0${trimmed.isEmpty ? '' : '.$trimmed'}';
    return neg ? '-$out' : out;
  } else {
    final whole = u.substring(0, u.length - decimals);
    final frac = u.substring(u.length - decimals);
    final limited = frac.substring(0, math.min(frac.length, maxFractionDigits));
    final trimmed = limited.replaceFirst(RegExp(r'0+$'), '');
    final out = trimmed.isEmpty ? whole : '$whole.$trimmed';
    return neg ? '-$out' : out;
  }
}
