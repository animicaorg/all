// Settings — accounts list, password change, import/export wallets.json,
// network info, wipe.

import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../constants.dart';
import '../services/import_export.dart';
import '../state/auth_state.dart';
import '../state/wallet_state.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final accountsAsync = ref.watch(accountsProvider);
    final activeAddr = ref.watch(activeAddressProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          const _SectionHeader('Accounts'),
          accountsAsync.when(
            loading: () => const Padding(
              padding: EdgeInsets.all(16),
              child: LinearProgressIndicator(),
            ),
            error: (e, _) =>
                Padding(padding: const EdgeInsets.all(16), child: Text('error: $e')),
            data: (accs) {
              if (accs.isEmpty) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('No accounts. Use Wallet → Create.'),
                );
              }
              return Column(
                children: accs.map((a) {
                  final isActive = a.address == activeAddr;
                  return ListTile(
                    leading: Icon(
                      isActive ? Icons.radio_button_checked : Icons.radio_button_off,
                      color: isActive
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.outline,
                    ),
                    title: Text(a.label),
                    subtitle: Text(
                      '${a.address.substring(0, 14)}…  ·  ${a.algName}',
                      style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
                    ),
                    trailing: IconButton(
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _confirmRemove(context, ref, a.address, a.label),
                    ),
                    onTap: () =>
                        ref.read(activeAddressProvider.notifier).state = a.address,
                  );
                }).toList(),
              );
            },
          ),
          ListTile(
            leading: const Icon(Icons.add),
            title: const Text('Create new wallet'),
            onTap: () async {
              final label = await _askText(context, 'New wallet label', 'Account ${(accountsAsync.value?.length ?? 0) + 1}');
              if (label == null) return;
              await ref.read(accountsProvider.notifier).createSphincsAccount(label);
            },
          ),

          const _SectionHeader('Import / Export'),
          ListTile(
            leading: const Icon(Icons.file_upload_outlined),
            title: const Text('Export wallets.json'),
            subtitle: const Text('Compatible with the CLI ~/.animica/wallets.json'),
            onTap: () => _exportWalletsJson(context, ref),
          ),
          ListTile(
            leading: const Icon(Icons.file_download_outlined),
            title: const Text('Import wallets.json'),
            subtitle: const Text('Pick a file or paste its contents'),
            onTap: () => _importWalletsJson(context, ref),
          ),

          const _SectionHeader('Security'),
          ListTile(
            leading: const Icon(Icons.lock_outline),
            title: const Text('Lock wallet'),
            onTap: () => ref.read(authProvider.notifier).lock(),
          ),
          ListTile(
            leading: Icon(Icons.delete_forever_outlined,
                color: Theme.of(context).colorScheme.error),
            title: Text('Wipe all data',
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
            subtitle: const Text(
                'Erases password and all stored keys. Export first if you want to keep them.'),
            onTap: () => _confirmWipe(context, ref),
          ),

          const _SectionHeader('Network'),
          ListTile(
            leading: const Icon(Icons.dns_outlined),
            title: const Text('RPC endpoint'),
            subtitle: Text(AnimicaConfig.rpcUrl,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
          ),
          ListTile(
            leading: const Icon(Icons.link),
            title: const Text('Chain'),
            subtitle: Text(
                '${AnimicaConfig.chainName} (id ${AnimicaConfig.chainId})'),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }

  Future<String?> _askText(BuildContext context, String title, String initial) async {
    final ctrl = TextEditingController(text: initial);
    return showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: TextField(controller: ctrl, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(c, ctrl.text.trim()), child: const Text('OK')),
        ],
      ),
    );
  }

  Future<void> _confirmRemove(BuildContext context, WidgetRef ref, String address, String label) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Remove wallet?'),
        content: Text(
            'Removes "$label" from this device. There\'s no recovery — back up first if the keys aren\'t exported.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: Theme.of(c).colorScheme.error),
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await ref.read(accountsProvider.notifier).remove(address);
    }
  }

  Future<void> _confirmWipe(BuildContext context, WidgetRef ref) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Wipe wallet?'),
        content: const Text(
            'This deletes ALL accounts AND the password. There is no recovery. '
            'If you have not exported wallets.json, your keys are gone forever.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: Theme.of(c).colorScheme.error),
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Wipe'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await ref.read(authProvider.notifier).wipe();
    }
  }

  Future<void> _exportWalletsJson(BuildContext context, WidgetRef ref) async {
    final accs = ref.read(accountsProvider).value ?? const [];
    if (accs.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No accounts to export.')),
      );
      return;
    }
    final body = exportWalletsJson(accs);
    // Two ways to deliver: copy to clipboard + share to a file destination.
    try {
      final dir = Directory.systemTemp;
      final f = File('${dir.path}/animica-wallets-${DateTime.now().millisecondsSinceEpoch}.json');
      await f.writeAsString(body);
      await Share.shareXFiles(
        [XFile(f.path, mimeType: 'application/json', name: 'wallets.json')],
        subject: 'Animica wallets.json',
        text:
            'Animica wallet export — keep this file safe. Anyone with it can spend your ANM.',
      );
    } catch (e) {
      // Fall back to clipboard if Share isn't available
      await Clipboard.setData(ClipboardData(text: body));
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Copied to clipboard (Share unavailable: $e)')),
      );
    }
  }

  Future<void> _importWalletsJson(BuildContext context, WidgetRef ref) async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.folder_open),
              title: const Text('Pick a wallets.json file'),
              onTap: () => Navigator.pop(c, 'file'),
            ),
            ListTile(
              leading: const Icon(Icons.content_paste),
              title: const Text('Paste JSON text'),
              onTap: () => Navigator.pop(c, 'paste'),
            ),
          ],
        ),
      ),
    );
    if (choice == null) return;

    String? text;
    if (choice == 'file') {
      final res = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['json'],
      );
      final path = res?.files.single.path;
      if (path == null) return;
      text = await File(path).readAsString();
    } else {
      final pasted = await _askMultilineText(context);
      if (pasted == null || pasted.trim().isEmpty) return;
      text = pasted;
    }

    final result = importWalletsJson(text);
    if (result.accounts.isNotEmpty) {
      await ref.read(accountsProvider.notifier).addAll(result.accounts);
    }
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Imported ${result.imported}'
          '${result.skipped > 0 ? ', skipped ${result.skipped}' : ''}'
          '${result.errors.isNotEmpty ? ' (errors: ${result.errors.first})' : ''}',
        ),
      ),
    );
  }

  Future<String?> _askMultilineText(BuildContext context) async {
    final ctrl = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Paste wallets.json'),
        content: SizedBox(
          width: double.maxFinite,
          child: TextField(
            controller: ctrl,
            maxLines: 10,
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              hintText: '{"wallets": [...]}',
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(c, ctrl.text),
              child: const Text('Import')),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String label;
  const _SectionHeader(this.label);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
      child: Text(
        label.toUpperCase(),
        style: TextStyle(
          color: Theme.of(context).colorScheme.primary,
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.7,
        ),
      ),
    );
  }
}
