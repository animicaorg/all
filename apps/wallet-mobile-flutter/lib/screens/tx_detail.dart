// Single-transaction detail: status, timestamp, confirmations, block
// height, amount, fee, addresses, hash — refreshed live against
// tx.getStatus (tx.getReceipt is useless: the node returns status:null
// even for mined txs) — with an explorer link at the bottom.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../constants.dart';
import '../services/rpc.dart';
import '../services/tx_history.dart';
import '../state/wallet_state.dart';
import 'history.dart';

class TxDetailScreen extends ConsumerStatefulWidget {
  final String hash;
  const TxDetailScreen({super.key, required this.hash});
  @override
  ConsumerState<TxDetailScreen> createState() => _TxDetailScreenState();
}

class _TxDetailScreenState extends ConsumerState<TxDetailScreen> {
  TxRecord? _rec;
  bool _loading = true;
  // Shown when there is nothing to render — says WHY (unknown hash vs
  // unreachable network), which are very different situations for a user
  // deciding whether to retry a payment.
  String _missMessage = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // Capture everything ref-dependent before the first await; this State
    // can be disposed while the store/RPC calls are in flight.
    final rpc = ref.read(rpcProvider);
    final addr = ref.read(activeAccountProvider)?.address;

    TxRecord? rec =
        addr == null ? null : await TxHistoryStore.find(addr, widget.hash);

    // Live refresh: the stored status is a snapshot; the chain moved on.
    // txStatus returns null when the node is unreachable OR errored.
    Map<String, dynamic>? st;
    try {
      st = await rpc.txStatus(widget.hash);
    } catch (_) {
      st = null;
    }

    if (rec != null) {
      final upd = applyNodeStatus(rec, st,
          nowMs: DateTime.now().millisecondsSinceEpoch);
      if (upd != null) {
        rec = upd;
        await TxHistoryStore.patch(upd);
        if (mounted && addr != null) {
          ref.invalidate(txHistoryProvider(addr));
        }
      }
    } else if (st == null) {
      _missMessage = 'Could not reach the network to look up this '
          'transaction. Pull back and try again.';
    } else if ((st['status'] ?? '').toString() == 'not_found') {
      _missMessage = 'This transaction is not in the local history and the '
          'network does not know the hash.';
    } else {
      // The node knows it but this device never recorded it (other device,
      // cleared data). Show what the node can tell us — and only that: no
      // fabricated sender, no made-up amount.
      final height = st['included_height'] ?? st['blockNumber'];
      final status = (st['status'] ?? '').toString();
      final state = (st['state'] ?? '').toString();
      rec = TxRecord(
        hash: widget.hash,
        from: '(not recorded on this device)',
        to: '(not recorded on this device)',
        kind: 'transfer',
        amountNanos: BigInt.zero,
        feeNanos: BigInt.zero,
        timestampMs: 0,
        status: (status == 'finalized' ||
                status == 'confirmed' ||
                height != null)
            ? TxStatus.confirmed
            : (status == 'rejected' || state == 'rejected')
                ? TxStatus.rejected
                : TxStatus.pending,
        blockHeight: height is int ? height : null,
        confirmations:
            st['confirmations'] is int ? st['confirmations'] as int : null,
        note: st['reason']?.toString(),
      );
    }
    if (mounted) {
      setState(() {
        _rec = rec;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Transaction')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _rec == null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      _missMessage,
                      textAlign: TextAlign.center,
                      style: TextStyle(color: theme.colorScheme.outline),
                    ),
                  ),
                )
              : _detail(context, _rec!),
    );
  }

  Widget _detail(BuildContext context, TxRecord rec) {
    final theme = Theme.of(context);
    final received = rec.direction == TxDirection.received;
    final dark = theme.brightness == Brightness.dark;
    final ts = rec.timestampMs > 0
        ? DateFormat('d MMM yyyy, HH:mm:ss')
            .format(DateTime.fromMillisecondsSinceEpoch(rec.timestampMs))
        : '—';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Text(
              rec.amountNanos > BigInt.zero
                  ? '${received ? '+' : '−'}${formatAnm(rec.amountNanos, maxDecimals: 9)} ANM'
                  : rec.kind,
              style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                  color: received
                      ? (dark ? Colors.green.shade300 : Colors.green.shade700)
                      : null),
            ),
            const Spacer(),
            TxStatusChip(status: rec.status),
          ],
        ),
        if (rec.status == TxStatus.dropped) ...[
          const SizedBox(height: 8),
          Text(
            'The network dropped this transaction without mining it — the '
            'funds did not leave your account.',
            style: TextStyle(color: theme.colorScheme.error, fontSize: 13),
          ),
        ],
        if (rec.status == TxStatus.rejected && rec.note != null) ...[
          const SizedBox(height: 8),
          Text('Rejected: ${rec.note}',
              style:
                  TextStyle(color: theme.colorScheme.error, fontSize: 13)),
        ],
        const SizedBox(height: 20),
        _row(context, 'Status', rec.status),
        _row(context, 'Timestamp', ts),
        _row(
            context,
            'Confirmations',
            rec.status == TxStatus.confirmed
                ? (rec.confirmations?.toString() ?? '—')
                : '0'),
        _row(context, 'Block height',
            rec.blockHeight?.toString() ?? 'not included'),
        if (rec.amountNanos > BigInt.zero)
          _row(context, 'Amount',
              '${formatAnm(rec.amountNanos, maxDecimals: 9)} ANM'),
        _row(
            context,
            received ? 'Network fee (paid by sender)' : 'Network fee',
            rec.feeNanos > BigInt.zero
                ? '${formatAnm(rec.feeNanos, maxDecimals: 9)} ANM'
                : '—'),
        _row(context, 'From', rec.from, mono: true, copyable: true),
        _row(context, 'To', rec.to, mono: true, copyable: true),
        _row(context, 'Transaction hash', rec.hash,
            mono: true, copyable: true),
        const SizedBox(height: 24),
        OutlinedButton.icon(
          icon: const Icon(Icons.open_in_new, size: 18),
          label: const Text('View in Explorer'),
          style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          onPressed: () => launchUrl(
            Uri.parse(
                AnimicaConfig.explorerTxUrl.replaceAll('{txHash}', rec.hash)),
            mode: LaunchMode.externalApplication,
          ),
        ),
      ],
    );
  }

  Widget _row(BuildContext context, String label, String value,
      {bool mono = false, bool copyable = false}) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: theme.textTheme.labelMedium
                  ?.copyWith(color: theme.colorScheme.outline)),
          const SizedBox(height: 2),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: SelectableText(
                  value,
                  style: TextStyle(
                    fontFamily: mono ? 'monospace' : null,
                    fontSize: mono ? 12 : 14,
                  ),
                ),
              ),
              if (copyable)
                IconButton(
                  icon: const Icon(Icons.copy, size: 16),
                  padding: EdgeInsets.zero,
                  constraints:
                      const BoxConstraints(minWidth: 32, minHeight: 32),
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: value));
                    if (!context.mounted) return;
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                        content: Text('$label copied'),
                        duration: const Duration(seconds: 1)));
                  },
                ),
            ],
          ),
        ],
      ),
    );
  }
}
