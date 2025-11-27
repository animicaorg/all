import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../constants.dart';
import '../../keyring/keyring.dart';
import '../../keyring/pq_sign.dart';
import '../../state/account_state.dart';
import '../../state/providers.dart';
import '../../tx/tx_builder.dart';
import '../../tx/tx_signbytes.dart';
import '../../tx/tx_types.dart';
import '../../codec/cbor.dart';
import '../../utils/format.dart';
import '../../widgets/qrcode/qr_view.dart';
import '../common/placeholder_page.dart';

class SendPage extends ConsumerStatefulWidget {
  const SendPage({super.key});

  @override
  ConsumerState<SendPage> createState() => _SendPageState();
}

class _SendPageState extends ConsumerState<SendPage> {
  final _toCtrl = TextEditingController();
  final _amtCtrl = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _toCtrl.dispose();
    _amtCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final active = ref.watch(activeAccountProvider);
    final balance = ref.watch(activeBalanceProvider);

    if (active == null || balance == null) {
      return const PlaceholderPage(
        title: 'Send',
        icon: Icons.send_outlined,
        message: 'Add or select an account before sending funds.',
      );
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Send')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          Card(
            margin: const EdgeInsets.only(bottom: 16),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('From', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  Text(active.label.isEmpty ? active.address : active.label,
                      style: Theme.of(context).textTheme.bodyLarge),
                  const SizedBox(height: 6),
                  Text('Balance: ${formatAmountWithSymbol(balance.amount)}',
                      style: Theme.of(context).textTheme.bodyMedium),
                ],
              ),
            ),
          ),

          TextField(
            controller: _toCtrl,
            decoration: InputDecoration(
              labelText: 'To address',
              hintText: 'am1…',
              suffixIcon: IconButton(
                tooltip: 'Scan QR',
                icon: const Icon(Icons.qr_code_scanner_outlined),
                onPressed: () async {
                  final scanned = await showQrScanSheet(context, title: 'Scan destination');
                  if (scanned != null && mounted) {
                    setState(() => _toCtrl.text = scanned.trim());
                  }
                },
              ),
            ),
            keyboardType: TextInputType.text,
            autocorrect: false,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _amtCtrl,
            decoration: const InputDecoration(
              labelText: 'Amount',
              hintText: '0.0',
              suffixText: 'ANM',
            ),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
          ),

          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],

          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _busy ? null : () => _submit(active, balance),
            icon: _busy
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.send),
            label: const Text('Send'),
          ),
        ],
      ),
    );
  }

  Future<void> _submit(WalletAccount active, AccountBalance balance) async {
    final to = _toCtrl.text.trim();
    final amt = parseAmountToAtto(_amtCtrl.text);
    final chainId = ref.read(chainIdProvider);

    if (to.isEmpty) {
      setState(() => _error = 'Destination address is required');
      return;
    }
    if (amt == null || amt <= BigInt.zero) {
      setState(() => _error = 'Enter a valid amount');
      return;
    }
    if (amt > balance.amount) {
      setState(() => _error = 'Insufficient balance');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final nonce = BigInt.from(balance.nonce);
      final tx = TxBuilder.transfer(
        chainId: chainId,
        nonce: nonce,
        to: to,
        value: amt,
        gasLimit: BigInt.from(Gas.defaultTransferLimit),
      );

      // Derive keys and sign
      final signer = PqSigner.dev();
      final kp = await keyring.deriveDilithium3(signer: signer, account: 0);
      final signBytes = TxSignBytes.encode(tx);
      final sig = await keyring.signDilithium3(
        signer: signer,
        account: 0,
        message: signBytes,
      );

      final signed = SignedTx(
        tx: tx,
        sig: Signature(
          algo: SigAlgo.dilithium3,
          pubkey: kp.publicKey,
          signature: sig.bytes,
        ),
      );

      final raw = Cbor.encode(signed.toJson());
      final txHash = await ref.read(txServiceProvider).sendRawTransaction(raw);

      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Transaction sent: $txHash')),
      );
      _amtCtrl.clear();
      _toCtrl.clear();
      ref.read(accountsStateProvider.notifier).refreshBalance(active.address);
    } catch (e) {
      if (mounted) {
        setState(() => _error = e.toString());
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
