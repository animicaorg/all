// Send — v0.1 stub.
//
// Real send needs:
//   1. The canonical sign-bytes encoder (pq.py.sign.build_sign_bytes Dart port)
//   2. tx envelope CBOR serialization
//   3. tx.sendRawTransaction broadcast
//
// All three are doable but each adds 100–200 lines. v0.2.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/address.dart';
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

  @override
  void dispose() {
    _to.dispose();
    _amount.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final from = ref.watch(activeAccountProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Send ANM')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: ListView(
          children: [
            if (from != null) ...[
              Text('From',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: Theme.of(context).colorScheme.outline)),
              const SizedBox(height: 4),
              Text(from.label, style: const TextStyle(fontWeight: FontWeight.w600)),
              Text(from.address,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
              const SizedBox(height: 20),
            ],
            TextField(
              controller: _to,
              decoration: const InputDecoration(
                labelText: 'To address',
                hintText: 'anim1…',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _amount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: 'Amount (ANM)',
                border: OutlineInputBorder(),
                suffixText: 'ANM',
              ),
            ),
            if (_err != null) ...[
              const SizedBox(height: 12),
              Text(_err!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: 20),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Icon(Icons.construction,
                      size: 18, color: Theme.of(context).colorScheme.outline),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Send isn\'t wired yet in this build. Use the CLI '
                      '`animica wallet send` for now — the tx envelope encoder '
                      'lands in v0.2.',
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.outline, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            FilledButton(
              onPressed: () {
                setState(() {
                  if (!isValidAnimAddress(_to.text.trim())) {
                    _err = 'Invalid recipient address.';
                  } else if (double.tryParse(_amount.text.trim()) == null) {
                    _err = 'Invalid amount.';
                  } else {
                    _err = 'Send not yet wired — see CLI workaround.';
                  }
                });
              },
              child: const Text('Send'),
            ),
          ],
        ),
      ),
    );
  }
}
