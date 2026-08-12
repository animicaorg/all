// Transaction history — every tx this device broadcast, newest first,
// grouped by day. Tapping a row opens the full detail page.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../services/rpc.dart';
import '../services/tx_history.dart';
import '../state/wallet_state.dart';

class HistoryScreen extends ConsumerStatefulWidget {
  const HistoryScreen({super.key});
  @override
  ConsumerState<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  @override
  void initState() {
    super.initState();
    // Pending records go stale fast (the node forgets rejected hashes after
    // its TTL) — refresh them as soon as the screen opens.
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  Future<void> _refresh() async {
    final addr = ref.read(activeAccountProvider)?.address;
    if (addr == null) return;
    // signAndBroadcast writes records from EVERY flow (store checkout, dapp
    // browser, DEX, connect) and cannot touch this provider — invalidate
    // before reading so a freshly broadcast tx shows up even when no status
    // changed, then again if the node moved something forward.
    ref.invalidate(txHistoryProvider(addr));
    final changed =
        await TxHistoryStore.refreshPending(ref.read(rpcProvider), addr);
    if (!mounted) return;
    if (changed) ref.invalidate(txHistoryProvider(addr));
  }

  @override
  Widget build(BuildContext context) {
    final active = ref.watch(activeAccountProvider);
    if (active == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Transaction history')),
        body: const Center(child: Text('No active account.')),
      );
    }
    final history = ref.watch(txHistoryProvider(active.address));
    return Scaffold(
      appBar: AppBar(title: const Text('Transaction history')),
      body: history.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text('Could not load history: $e')),
        data: (recs) {
          if (recs.isEmpty) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  'No transactions yet.\n\nTransactions sent from this '
                  'device will appear here. Incoming transfers are not '
                  'tracked — check the explorer for the full on-chain view.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.outline),
                ),
              ),
            );
          }
          final rows = _withDayHeaders(recs);
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              itemCount: rows.length,
              itemBuilder: (context, i) {
                final row = rows[i];
                if (row is String) {
                  return Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
                    child: Text(row,
                        style: Theme.of(context)
                            .textTheme
                            .labelMedium
                            ?.copyWith(
                                color:
                                    Theme.of(context).colorScheme.outline)),
                  );
                }
                return _TxRow(rec: row as TxRecord);
              },
            ),
          );
        },
      ),
    );
  }

  /// Flatten records into [String dayHeader, TxRecord, TxRecord, …].
  List<Object> _withDayHeaders(List<TxRecord> recs) {
    final out = <Object>[];
    String? lastDay;
    for (final r in recs) {
      final day = _dayLabel(r.timestampMs);
      if (day != lastDay) {
        out.add(day);
        lastDay = day;
      }
      out.add(r);
    }
    return out;
  }

  String _dayLabel(int ms) {
    final d = DateTime.fromMillisecondsSinceEpoch(ms);
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final that = DateTime(d.year, d.month, d.day);
    if (that == today) return 'Today';
    if (that == today.subtract(const Duration(days: 1))) return 'Yesterday';
    return DateFormat('d MMM yyyy').format(d);
  }
}

class _TxRow extends StatelessWidget {
  final TxRecord rec;
  const _TxRow({required this.rec});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final time = DateFormat('HH:mm')
        .format(DateTime.fromMillisecondsSinceEpoch(rec.timestampMs));
    final icon = switch (rec.kind) {
      'deploy' => Icons.upload_file,
      'call' => Icons.settings_ethernet,
      _ => Icons.call_made,
    };
    return ListTile(
      leading: CircleAvatar(
        backgroundColor: cs.surfaceContainerHighest,
        child: Icon(icon, size: 20, color: cs.onSurfaceVariant),
      ),
      title: Text(
        _shorten(rec.to),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 14),
      ),
      subtitle: Row(
        children: [
          Text(time, style: TextStyle(fontSize: 12, color: cs.outline)),
          const SizedBox(width: 8),
          TxStatusChip(status: rec.status),
        ],
      ),
      trailing: Text(
        // Full 9-decimal precision: the default 4 truncates a
        // 0.000021 ANM fee-sized send into a misleading '−0 ANM'.
        rec.amountNanos > BigInt.zero
            ? '−${formatAnm(rec.amountNanos, maxDecimals: 9)} ANM'
            : rec.kind,
        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
      ),
      onTap: () => context.push('/history/tx/${rec.hash}'),
    );
  }

  String _shorten(String s) => s.length > 24
      ? '${s.substring(0, 12)}…${s.substring(s.length - 8)}'
      : s;
}

/// Small colored status pill shared by the history list and detail page.
class TxStatusChip extends StatelessWidget {
  final String status;
  const TxStatusChip({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    final (String label, MaterialColor color) = switch (status) {
      TxStatus.confirmed => ('Confirmed', Colors.green),
      TxStatus.rejected => ('Rejected', Colors.red),
      TxStatus.dropped => ('Dropped', Colors.grey),
      _ => ('Pending', Colors.amber),
    };
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(dark ? 0.25 : 0.15),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(label,
          style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: dark ? color.shade300 : color.shade700)),
    );
  }
}
