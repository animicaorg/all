import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../brand/brand_logo.dart';

/// BalanceCard — shows a token balance with fiat estimate and quick actions.
///
/// Props:
///   • symbol            e.g. "ANM"
///   • decimals          e.g. 18
///   • balanceUnits      minimal-units as decimal string (e.g. "1230000000000000000")
///   • fiatPerTokenUsd   optional USD price per token (for estimate)
///   • address           optional address to show + copy
///   • onRefresh         optional refresh callback (shows refresh icon)
///   • dense             tighter padding & smaller typography
///
/// Example:
/// ```dart
/// BalanceCard(
///   symbol: 'ANM',
///   decimals: 18,
///   balanceUnits: '1234500000000000000',
///   fiatPerTokenUsd: 0.14,
///   address: 'am1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
///   onRefresh: _reload,
/// )
/// ```
class BalanceCard extends StatelessWidget {
  const BalanceCard({
    super.key,
    required this.symbol,
    required this.decimals,
    required this.balanceUnits,
    this.fiatPerTokenUsd,
    this.address,
    this.onRefresh,
    this.dense = false,
    this.leading,
  });

  final String symbol;
  final int decimals;
  final String balanceUnits;
  final double? fiatPerTokenUsd;
  final String? address;
  final VoidCallback? onRefresh;
  final bool dense;
  final Widget? leading;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    final pretty = _prettyUnits(balanceUnits, decimals, maxFractionDigits: 6);
    final amountDouble = _unitsToDoubleSafe(balanceUnits, decimals);
    final fiat = (fiatPerTokenUsd != null && amountDouble != null)
        ? amountDouble * fiatPerTokenUsd!
        : null;

    final pad = dense ? const EdgeInsets.all(12) : const EdgeInsets.all(16);
    final titleStyle = dense
        ? theme.textTheme.titleMedium
        : theme.textTheme.headlineSmall;
    final subStyle = theme.textTheme.bodyMedium?.copyWith(
      color: theme.textTheme.bodyMedium?.color?.withOpacity(0.72),
    );

    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
        boxShadow: [
          BoxShadow(
            color: cs.shadow.withOpacity(0.05),
            blurRadius: 16,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      padding: pad,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          leading ??
              const AnimicaLogo(
                size: 36,
                drawGlow: false,
                drawRing: true,
              ),
          const SizedBox(width: 12),
          Expanded(
            child: _BalanceTexts(
              symbol: symbol,
              prettyAmount: pretty,
              fiat: fiat,
              subStyle: subStyle,
              titleStyle: titleStyle,
              address: address,
            ),
          ),
          const SizedBox(width: 8),
          if (onRefresh != null)
            IconButton(
              tooltip: 'Refresh',
              onPressed: onRefresh,
              icon: const Icon(Icons.refresh),
            ),
        ],
      ),
    );
  }
}

class _BalanceTexts extends StatelessWidget {
  const _BalanceTexts({
    required this.symbol,
    required this.prettyAmount,
    required this.fiat,
    required this.titleStyle,
    required this.subStyle,
    required this.address,
  });

  final String symbol;
  final String prettyAmount;
  final double? fiat;
  final TextStyle? titleStyle;
  final TextStyle? subStyle;
  final String? address;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[
      Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Flexible(
            child: Text(
              '$prettyAmount $symbol',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: titleStyle,
            ),
          ),
          if (fiat != null) ...[
            const SizedBox(width: 8),
            Text(
              '≈ ${_formatUsd(fiat!)}',
              style: subStyle,
            ),
          ],
        ],
      ),
    ];

    if (address != null && address!.isNotEmpty) {
      children.add(const SizedBox(height: 6));
      children.add(
        Row(
          children: [
            Expanded(
              child: Text(
                _shortenAddress(address!),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: subStyle,
              ),
            ),
            IconButton(
              tooltip: 'Copy address',
              icon: const Icon(Icons.copy, size: 18),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: address!));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Address copied')),
                  );
                }
              },
            ),
          ],
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }
}

// ----------------- helpers -----------------

String _prettyUnits(String units, int decimals, {int maxFractionDigits = 6}) {
  // Strip leading zeros
  String u = units.trim();
  bool neg = u.startsWith('-');
  if (neg) u = u.substring(1);
  u = u.replaceFirst(RegExp(r'^0+'), '');
  if (u.isEmpty) u = '0';

  // Split whole/frac
  if (decimals <= 0) {
    return neg ? '-$u' : u;
  }
  if (u.length <= decimals) {
    // 0.<pad>digits
    final frac = u.padLeft(decimals, '0');
    final trimmed = _trimFrac(frac, maxFractionDigits: maxFractionDigits);
    final out = '0${trimmed.isEmpty ? '' : '.$trimmed'}';
    return neg ? '-$out' : out;
  } else {
    final whole = u.substring(0, u.length - decimals);
    final frac = u.substring(u.length - decimals);
    final trimmed = _trimFrac(frac, maxFractionDigits: maxFractionDigits);
    final out = trimmed.isEmpty ? whole : '$whole.$trimmed';
    return neg ? '-$out' : out;
  }
}

String _trimFrac(String frac, {int maxFractionDigits = 6}) {
  // Limit fractional length, then trim trailing zeros
  final limited = frac.substring(0, math.min(frac.length, maxFractionDigits));
  return limited.replaceFirst(RegExp(r'0+$'), '');
}

double? _unitsToDoubleSafe(String units, int decimals) {
  try {
    final neg = units.startsWith('-');
    final s = neg ? units.substring(1) : units;
    final bi = BigInt.parse(s);
    final d = bi.toDouble() / math.pow(10.0, decimals);
    return neg ? -d : d;
  } catch (_) {
    return null;
  }
}

String _formatUsd(double v) {
  // Simple USD formatter: $1,234.56 (approx; no intl dependency)
  final sign = v < 0 ? '-' : '';
  final abs = v.abs();
  final whole = abs.floor();
  final frac = ((abs - whole) * 100).round(); // 2 decimals

  String withCommas(int n) {
    final s = n.toString();
    final buf = StringBuffer();
    for (int i = 0; i < s.length; i++) {
      final idx = s.length - i;
      buf.write(s[i]);
      final left = s.length - i - 1;
      if (left > 0 && left % 3 == 0) buf.write(',');
    }
    return buf.toString();
  }

  final w = withCommas(whole);
  final f = frac.toString().padLeft(2, '0');
  return '$sign\$$w.$f';
}

String _shortenAddress(String addr) {
  if (addr.length <= 16) return addr;
  if (addr.startsWith('0x') || addr.startsWith('0X')) {
    return '${addr.substring(0, 10)}…${addr.substring(addr.length - 8)}';
  }
  // am1… style
  return '${addr.substring(0, 8)}…${addr.substring(addr.length - 6)}';
}
