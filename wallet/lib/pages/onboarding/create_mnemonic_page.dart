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
import '../../keyring/mnemonic.dart';

class CreateMnemonicPage extends ConsumerStatefulWidget {
  const CreateMnemonicPage({super.key});

  @override
  ConsumerState<CreateMnemonicPage> createState() => _CreateMnemonicPageState();
}

class _CreateMnemonicPageState extends ConsumerState<CreateMnemonicPage> {
  String? _mnemonic;
  String? _error;
  bool _saving = false;
  final _passwordCtrl = TextEditingController();
  final _confirmCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _generate();
  }

  @override
  void dispose() {
    _passwordCtrl.dispose();
    _confirmCtrl.dispose();
    super.dispose();
  }

  void _generate() {
    try {
      final phrase = Mnemonic.generate();
      setState(() {
        _mnemonic = phrase;
        _error = null;
      });
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
            const SizedBox(height: 20),
            Text('Set a wallet password', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            TextField(
              controller: _passwordCtrl,
              decoration: const InputDecoration(
                labelText: 'Password',
                hintText: 'At least 8 characters',
              ),
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _confirmCtrl,
              decoration: const InputDecoration(
                labelText: 'Confirm password',
              ),
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
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
      final phrase = _mnemonic;
      if (phrase == null) {
        throw Exception('No recovery phrase available. Try regenerating.');
      }

      final password = _passwordCtrl.text;
      if (password.isEmpty) {
        throw Exception('Password is required');
      }
      if (password.length < 8) {
        throw Exception('Password must be at least 8 characters');
      }
      if (password != _confirmCtrl.text) {
        throw Exception('Passwords do not match');
      }
      if (await keyring.hasWallet()) {
        throw Exception('A wallet already exists on this device. Wipe it before creating a new one.');
      }

      await keyring.importWallet(mnemonic: phrase, passphrase: password);

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
