// Send ANM. Builds a kind=0 transfer body, signs locally, broadcasts via
// the multi-endpoint RPC client.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:url_launcher/url_launcher.dart';

import '../constants.dart';
import '../services/address.dart';
import '../services/rpc.dart';
import '../services/signer.dart';
import '../state/wallet_state.dart';

class SendScreen extends ConsumerStatefulWidget {
  const SendScreen({super.key});
  @override
  ConsumerState<SendScreen> createState() => _SendScreenState();
}

class _SendScreenState extends ConsumerState<SendScreen> {
  final _to = TextEditingController();
  final _amount = TextEditingController();
  String? _err;
  bool _busy = false;
  String? _txHash;
  String? _txStatus;
  bool _txTerminal = false;
  int _trackGen = 0;

  /// Worst-case network fee for a plain transfer, in nanos.
  static final BigInt _feeNanos =
      BigInt.from(kDefaultGasPrice) * BigInt.from(kDefaultTransferGasLimit);

  @override
  void dispose() {
    _to.dispose();
    _amount.dispose();
    super.dispose();
  }

  Future<void> _scanQr() async {
    final scanned = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (c) => const _QrScanner()),
    );
    if (scanned != null && mounted) {
      setState(() => _to.text = scanned);
    }
  }

  Future<void> _send() async {
    setState(() {
      _err = null;
      _txHash = null;
      _txStatus = null;
      _txTerminal = false;
      _trackGen++;
    });
    final from = ref.read(activeAccountProvider);
    if (from == null) {
      setState(() => _err = 'No active account.');
      return;
    }
    final to = _to.text.trim();
    if (!isValidAnimAddress(to)) {
      setState(() => _err = 'Invalid recipient address.');
      return;
    }
    final amountStr = _amount.text.trim();
    final amount = double.tryParse(amountStr);
    if (amount == null || amount <= 0) {
      setState(() => _err = 'Amount must be a positive number.');
      return;
    }
    // ANM → nanos. Avoid floating-point loss for the last few digits.
    final BigInt amountNanos;
    try {
      amountNanos = _anmToNanos(amountStr);
    } catch (e) {
      setState(() => _err = 'Amount has too many decimals (max 9).');
      return;
    }

    setState(() => _busy = true);
    try {
      final rpc = ref.read(rpcProvider);
      // The chain identity (genesis hash / network / fork id) is what the
      // signature is bound to — see AnimicaChainContext. It is cached per RPC
      // client, so this is a round trip once per session, not once per send.
      // Never fall back to a guess: signing without it produces a tx the node
      // rejects with BadSignature.
      final ctx = await chainContextFor(rpc);
      // Refuse to build a tx the network can accept but never mine: the
      // chain debits amount + gasLimit×gasPrice, and a tx that can't cover
      // both is silently starved out of every block, then evicted with no
      // trace — the "stuck in mempool, then vanished" failure users hit.
      final balance = await rpc.getBalance(from.address);
      if (amountNanos + _feeNanos > balance) {
        setState(() => _err =
            'Amount plus the network fee (${formatAnm(_feeNanos, maxDecimals: 9)} ANM) '
            'exceeds your balance of ${formatAnm(balance, maxDecimals: 9)} ANM.');
        return;
      }
      final nonce = await rpc.getPendingNonce(from.address);
      final body = buildTransferBody(
        from: from.address,
        to: to,
        amountNanos: amountNanos,
        nonce: nonce,
        chainId: ctx.chainId,
      );
      final txHash = await signAndBroadcast(
        rpc: rpc,
        account: from,
        body: body,
        chainContext: ctx,
      );
      // Best-effort balance refresh after a few seconds.
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted) ref.refresh(balanceProvider);
      });
      if (mounted) {
        setState(() {
          _txHash = txHash;
          _txStatus = 'Submitted — waiting for confirmation…';
        });
        // Track the tx to a terminal state instead of showing "Submitted"
        // forever: mined txs confirm, dropped ones say so out loud.
        unawaited(_trackTx(rpc, txHash, _trackGen));
      }
    } on RpcError catch (e) {
      // Covers both the chain-identity fetch and the broadcast; the RPC
      // messages are already written for a human ("Could not read the chain
      // identity needed to sign …"), so don't bury them under a wrong prefix.
      if (mounted) setState(() => _err = 'Could not send: ${e.message}');
    } catch (e) {
      if (mounted) setState(() => _err = 'Send failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Poll tx.getStatus until the tx confirms, is rejected, or drops out of
  /// the network. Blocks land ~every 80 s, so poll gently for ~4 minutes.
  /// tx.getReceipt is useless here — the node returns status:null even for
  /// mined txs — tx.getStatus is the reliable surface.
  Future<void> _trackTx(RpcClient rpc, String txHash, int gen) async {
    var notFoundStreak = 0;
    for (var i = 0; i < 40; i++) {
      await Future.delayed(const Duration(seconds: 6));
      if (!mounted || _trackGen != gen) return;
      final st = await rpc.txStatus(txHash);
      if (!mounted || _trackGen != gen) return;
      final status = (st?['status'] ?? '').toString();
      final state = (st?['state'] ?? '').toString();
      if (status == 'finalized' ||
          status == 'confirmed' ||
          st?['included_height'] != null) {
        final h = st?['included_height'] ?? st?['blockNumber'];
        setState(() {
          _txStatus = '✓ Confirmed${h != null ? ' in block $h' : ''}';
          _txTerminal = true;
        });
        ref.refresh(balanceProvider);
        return;
      }
      if (status == 'rejected' || state == 'rejected') {
        final reason =
            st?['reason'] ?? st?['rejection_details'] ?? 'unknown reason';
        setState(() {
          _txStatus = '✗ Rejected by the network ($reason). '
              'Funds have NOT left your account.';
          _txTerminal = true;
        });
        return;
      }
      if (status == 'not_found') {
        // One not_found can be propagation lag; a streak means the pool
        // silently evicted it. Say so instead of pretending it is pending.
        notFoundStreak++;
        if (notFoundStreak >= 3) {
          setState(() {
            _txStatus = '⚠ The network dropped this transaction without '
                'mining it. Funds have NOT left your account — check your '
                'balance before retrying.';
            _txTerminal = true;
          });
          ref.refresh(balanceProvider);
          return;
        }
      } else {
        notFoundStreak = 0;
      }
    }
    if (mounted && _trackGen == gen && !_txTerminal) {
      setState(() {
        _txStatus = 'Still unconfirmed after 4 minutes. Check the explorer '
            'before retrying — sending again would duplicate the payment.';
        _txTerminal = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final from = ref.watch(activeAccountProvider);
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Send ANM')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: ListView(
          children: [
            if (from != null) ...[
              Text('From',
                  style: theme.textTheme.labelMedium
                      ?.copyWith(color: theme.colorScheme.outline)),
              const SizedBox(height: 4),
              Text(from.label,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              Text(from.address,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
              const SizedBox(height: 20),
            ],
            TextField(
              controller: _to,
              decoration: InputDecoration(
                labelText: 'To address',
                hintText: 'anim1…',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.qr_code_scanner),
                  onPressed: _scanQr,
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _amount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: 'Amount',
                border: OutlineInputBorder(),
                suffixText: 'ANM',
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Network fee: ${formatAnm(_feeNanos, maxDecimals: 9)} ANM',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.outline),
            ),
            if (_err != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: theme.colorScheme.errorContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(_err!,
                    style: TextStyle(color: theme.colorScheme.onErrorContainer)),
              ),
            ],
            if (_txHash != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(_txStatus ?? '✓ Submitted',
                        style: TextStyle(
                            color: theme.colorScheme.onPrimaryContainer,
                            fontWeight: FontWeight.w700)),
                    const SizedBox(height: 4),
                    SelectableText(_txHash!,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 10)),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        TextButton.icon(
                          icon: const Icon(Icons.copy, size: 16),
                          label: const Text('Copy'),
                          onPressed: () => Clipboard.setData(
                              ClipboardData(text: _txHash!)),
                        ),
                        TextButton.icon(
                          icon: const Icon(Icons.open_in_new, size: 16),
                          label: const Text('Explorer'),
                          onPressed: () => launchUrl(
                            Uri.parse(AnimicaConfig.explorerTxUrl
                                .replaceAll('{txHash}', _txHash!)),
                            mode: LaunchMode.externalApplication,
                          ),
                        ),
                        const Spacer(),
                        TextButton(
                          onPressed: () => context.pop(),
                          child: const Text('Done'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 20),
            FilledButton(
              // Also disabled while a submitted tx is still unconfirmed:
              // re-sending "because nothing happened" is exactly how users
              // ended up with duplicate mined transfers.
              onPressed:
                  (_busy || (_txHash != null && !_txTerminal)) ? null : _send,
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              child: _busy || (_txHash != null && !_txTerminal)
                  ? const SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Send'),
            ),
          ],
        ),
      ),
    );
  }

  /// Convert a decimal ANM string into BigInt nanos. Rejects > 9 dp.
  BigInt _anmToNanos(String s) {
    final cleaned = s.trim();
    final dot = cleaned.indexOf('.');
    if (dot < 0) {
      return BigInt.parse(cleaned) * AnimicaConfig.nanosPerAnm;
    }
    final whole = cleaned.substring(0, dot);
    var frac = cleaned.substring(dot + 1);
    if (frac.length > 9) throw FormatException('too many decimals');
    frac = frac.padRight(9, '0');
    return BigInt.parse(whole) * AnimicaConfig.nanosPerAnm + BigInt.parse(frac);
  }
}

class _QrScanner extends StatelessWidget {
  const _QrScanner();
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Scan address')),
      body: MobileScanner(
        onDetect: (capture) {
          for (final b in capture.barcodes) {
            if (b.rawValue != null && b.rawValue!.isNotEmpty) {
              Navigator.of(context).pop(b.rawValue);
              return;
            }
          }
        },
      ),
    );
  }
}
