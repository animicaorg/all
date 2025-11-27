import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../keyring/keyring.dart';
import '../../keyring/pq_sign.dart';
import '../../router.dart';
import '../../state/account_state.dart';
import '../../crypto/sha3.dart' as hash;
import '../../crypto/bech32m.dart';

class CreateMnemonicPage extends ConsumerStatefulWidget {
  const CreateMnemonicPage({super.key});

  @override
  ConsumerState<CreateMnemonicPage> createState() => _CreateMnemonicPageState();
}

class _CreateMnemonicPageState extends ConsumerState<CreateMnemonicPage> {
  String? _mnemonic;
  String? _error;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _generate();
  }

  Future<void> _generate() async {
    try {
      final phrase = await keyring.createWallet();
      setState(() => _mnemonic = phrase);
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final words = _mnemonic?.split(' ') ?? const [];

    return Scaffold(
      appBar: AppBar(title: const Text('Create wallet')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Save your recovery phrase',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 12),
            Text(
              'Write these words down in order. Anyone with the phrase can control your funds.',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 16),
            if (_error != null)
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            if (words.isNotEmpty)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (var i = 0; i < words.length; i++)
                        Chip(label: Text('${i + 1}. ${words[i]}')),
                    ],
                  ),
                ),
              ),
            const Spacer(),
            Row(
              children: [
                TextButton.icon(
                  onPressed: words.isEmpty
                      ? null
                      : () => Clipboard.setData(ClipboardData(text: _mnemonic!)),
                  icon: const Icon(Icons.copy_all_outlined),
                  label: const Text('Copy phrase'),
                ),
                const Spacer(),
                FilledButton.icon(
                  onPressed: words.isEmpty || _saving ? null : _continue,
                  icon: _saving
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.check_circle_outline),
                  label: const Text('Continue'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _continue() async {
    setState(() {
      _saving = true;
      _error = null;
    });

    try {
      final signer = PqSigner.dev();
      final kp = await keyring.deriveDilithium3(signer: signer, account: 0);
      final addrBytes = hash.sha3_256(kp.publicKey);
      final address = AnimicaAddr.encodeFromBytes(addrBytes) ??
          (throw Exception('Failed to derive address'));

      ref.read(accountsStateProvider.notifier).addAccount(
            address: address,
            label: 'Main account',
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
      if (mounted) setState(() => _saving = false);
    }
  }
}
