import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../../keyring/keyring.dart';
import '../../state/providers.dart';

class SettingsPage extends ConsumerStatefulWidget {
  const SettingsPage({super.key});

  @override
  ConsumerState<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends ConsumerState<SettingsPage> {
  late final TextEditingController _rpcCtrl;
  late final TextEditingController _wsCtrl;
  bool _exporting = false;

  @override
  void initState() {
    super.initState();
    _rpcCtrl = TextEditingController();
    _wsCtrl = TextEditingController();
  }

  @override
  void dispose() {
    _rpcCtrl.dispose();
    _wsCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final rpcUrl = ref.watch(rpcUrlProvider);
    final wsUrl = ref.watch(wsUrlProvider);

    if (_rpcCtrl.text.isEmpty) _rpcCtrl.text = rpcUrl;
    if (_wsCtrl.text.isEmpty) _wsCtrl.text = wsUrl;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Wallet', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            'Manage your Animica keys and backups. Export a portable wallet file to store in a safe place.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _exporting ? null : _exportWalletFile,
            icon: _exporting
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.file_upload_outlined),
            label: const Text('Export wallet backup'),
          ),
          const SizedBox(height: 24),
          Text('Network', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            'Point the wallet at a different Animica RPC/WS endpoint when switching environments.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _rpcCtrl,
            decoration: const InputDecoration(labelText: 'RPC HTTP URL'),
            onChanged: (v) => ref.read(rpcUrlProvider.notifier).state = v.trim(),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _wsCtrl,
            decoration: const InputDecoration(labelText: 'WebSocket URL'),
            onChanged: (v) => ref.read(wsUrlProvider.notifier).state = v.trim(),
          ),
          const SizedBox(height: 24),
          Text('Diagnostics', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            'Keep this info handy when talking to support about Animica network issues.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 12),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.link),
            title: const Text('Current RPC'),
            subtitle: Text(rpcUrl),
          ),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.bolt),
            title: const Text('Current WebSocket'),
            subtitle: Text(wsUrl),
          ),
        ],
      ),
    );
  }

  Future<void> _exportWalletFile() async {
    setState(() => _exporting = true);
    try {
      final file = await keyring.exportWalletFile();
      final bytes = file.toBytes();
      await Share.shareXFiles([
        XFile.fromData(
          bytes,
          name: 'animica-wallet-backup.json',
          mimeType: 'application/json',
        ),
      ], text: 'Animica wallet backup file');
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to export wallet: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _exporting = false);
    }
  }
}
