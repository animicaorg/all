import 'package:flutter/material.dart';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../crypto/bech32m.dart';
import '../../crypto/sha3.dart' as hash;
import '../../keyring/keyring.dart';
import '../../keyring/pq_sign.dart';
import '../../router.dart';
import '../../state/account_state.dart';
import '../../utils/wallet_backup.dart';

class ImportWalletPage extends ConsumerStatefulWidget {
  const ImportWalletPage({super.key});

  @override
  ConsumerState<ImportWalletPage> createState() => _ImportWalletPageState();
}

class _ImportWalletPageState extends ConsumerState<ImportWalletPage> {
  PlatformFile? _selectedFile;
  WalletBackupFile? _parsed;
  String? _error;
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Import wallet')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Wallet backup file', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              'Choose the Animica wallet backup file (.json) that contains your encrypted keys.',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: _busy ? null : _pickFile,
              icon: const Icon(Icons.file_open_outlined),
              label: Text(_selectedFile?.name ?? 'Select backup file'),
            ),
            if (_parsed != null) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.check_circle, color: Colors.green),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Wallet file loaded (exported ${_parsed!.exportedAt.toLocal()})',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ),
                ],
              ),
            ],
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

  Future<void> _pickFile() async {
    try {
      final res = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['json'],
        withData: true,
      );
      if (res == null || res.files.isEmpty) return;
      final file = res.files.single;
      Uint8List? bytes = file.bytes;
      if (bytes == null) {
        final path = file.path;
        if (path == null) {
          throw const FormatException('Selected file is unreadable');
        }
        bytes = await File(path).readAsBytes();
      }
      final parsed = WalletBackupFile.parse(utf8.decode(bytes));
      setState(() {
        _selectedFile = file;
        _parsed = parsed;
        _error = null;
      });
    } catch (e) {
      setState(() {
        _selectedFile = null;
        _parsed = null;
        _error = 'Failed to read wallet file: $e';
      });
    }
  }

  Future<void> _submit() async {
    if (_parsed == null) {
      setState(() => _error = 'Select a valid wallet backup file first');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      await keyring.importWalletFile(_parsed!);

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
