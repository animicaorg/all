import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../crypto/bech32m.dart';
import '../../crypto/sha3.dart' as hash;
import '../../keyring/keyring.dart';
import '../../keyring/pq_sign.dart';
import '../../router.dart';
import '../../state/account_state.dart';

class ImportWalletPage extends ConsumerStatefulWidget {
  const ImportWalletPage({super.key});

  @override
  ConsumerState<ImportWalletPage> createState() => _ImportWalletPageState();
}

class _ImportWalletPageState extends ConsumerState<ImportWalletPage> {
  final _mnemonicCtrl = TextEditingController();
  final _passphraseCtrl = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _mnemonicCtrl.dispose();
    _passphraseCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Import wallet')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Recovery phrase', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            TextField(
              controller: _mnemonicCtrl,
              decoration: InputDecoration(
                hintText: 'twelve or twenty-four words',
                suffixIcon: IconButton(
                  tooltip: 'Paste',
                  icon: const Icon(Icons.paste),
                  onPressed: () async {
                    final clip = await Clipboard.getData('text/plain');
                    final text = clip?.text;
                    if (text != null && text.isNotEmpty) {
                      _mnemonicCtrl.text = text.trim();
                    }
                  },
                ),
              ),
              minLines: 2,
              maxLines: 4,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _passphraseCtrl,
              decoration: const InputDecoration(
                labelText: 'Passphrase (optional)',
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const Spacer(),
            FilledButton.icon(
              onPressed: _busy ? null : _submit,
              icon: _busy
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.check),
              label: const Text('Import'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submit() async {
    final phrase = _mnemonicCtrl.text.trim().toLowerCase();
    if (phrase.split(RegExp(r'\s+')).length < 12) {
      setState(() => _error = 'Enter a valid 12/24-word phrase');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      await keyring.importWallet(
        mnemonic: phrase,
        passphrase: _passphraseCtrl.text.trim(),
      );

      final signer = PqSigner.dev();
      final kp = await keyring.deriveDilithium3(signer: signer, account: 0);
      final addrBytes = hash.sha3_256(kp.publicKey);
      final address = AnimicaAddr.encodeFromBytes(addrBytes) ??
          (throw Exception('Failed to derive address'));

      ref.read(accountsStateProvider.notifier).addAccount(
            address: address,
            label: 'Imported account',
            watchOnly: false,
            meta: {
              'algo': 'dilithium3',
              'path': 'm/pq/dilithium3/0',
            },
          );

      if (mounted) context.go(Routes.onboardingSuccess);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
