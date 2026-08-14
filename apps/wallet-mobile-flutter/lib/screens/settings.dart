// Settings — accounts list, password change, import/export wallets.json,
// network info, wipe.

import 'dart:convert';
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
              final scheme = await _pickScheme(context);
              if (scheme == null) return;
              final label = await _askText(
                  context,
                  'New wallet label',
                  'Account ${(accountsAsync.value?.length ?? 0) + 1}');
              if (label == null) return;
              // Only ml_dsa_65 (0x1003) is spendable on-chain. The legacy
              // dilithium3/sphincs stub schemes are forgeable and permanently
              // strand funds, so new wallets are always ML-DSA-65.
              try {
                await ref
                    .read(accountsProvider.notifier)
                    .createMlDsa65Account(label);
              } catch (e) {
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Could not create wallet: $e')),
                );
              }
            },
          ),

          const _SectionHeader('Import / Export'),
          ListTile(
            leading: const Icon(Icons.file_upload_outlined),
            title: const Text('Export wallets.json'),
            subtitle: const Text(
                'Save to your device, share, or copy — CLI-compatible'),
            onTap: () => _exportWalletsJson(context, ref),
          ),
          ListTile(
            leading: const Icon(Icons.file_download_outlined),
            title: const Text('Import wallets.json'),
            subtitle: const Text('Pick a file or paste its contents'),
            onTap: () => _importWalletsJson(context, ref),
          ),

          const _SectionHeader('Security'),
          _BiometricToggle(),
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

  Future<String?> _pickScheme(BuildContext context) async {
    return showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('Choose signature scheme',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
            ListTile(
              leading: const Icon(Icons.verified_user),
              title: const Text('ML-DSA-65 (FIPS 204) — Recommended'),
              subtitle: const Text(
                  'Real post-quantum lattice signatures. Default since chain v2.'),
              onTap: () => Navigator.pop(c, 'ml_dsa_65'),
            ),
            const SizedBox(height: 8),
          ],
        ),
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

    final choice = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.download_outlined),
              title: const Text('Save to device'),
              subtitle: const Text('Pick a folder, e.g. Downloads'),
              onTap: () => Navigator.pop(c, 'save'),
            ),
            ListTile(
              leading: const Icon(Icons.share_outlined),
              title: const Text('Share…'),
              subtitle: const Text('Send to another app or device'),
              onTap: () => Navigator.pop(c, 'share'),
            ),
            ListTile(
              leading: const Icon(Icons.content_copy),
              title: const Text('Copy to clipboard'),
              onTap: () => Navigator.pop(c, 'copy'),
            ),
          ],
        ),
      ),
    );
    if (choice == null || !context.mounted) return;

    switch (choice) {
      case 'save':
        await _saveExportToDevice(context, body);
      case 'share':
        await _shareExport(context, body);
      case 'copy':
        await Clipboard.setData(ClipboardData(text: body));
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text(
                  'Copied. This contains your private keys — paste it somewhere safe.')),
        );
    }
  }

  /// Direct download: the system "create document" dialog (SAF on Android,
  /// Files on iOS) writes wallets.json wherever the user picks — no share
  /// sheet round-trip. Desktop dialogs only return a path, so write there.
  Future<void> _saveExportToDevice(BuildContext context, String body) async {
    try {
      final path = await FilePicker.platform.saveFile(
        dialogTitle: 'Save wallets.json',
        fileName: 'wallets.json',
        type: FileType.custom,
        allowedExtensions: const ['json'],
        bytes: Uint8List.fromList(utf8.encode(body)),
      );
      if (path == null) return; // user cancelled
      if (!Platform.isAndroid && !Platform.isIOS) {
        await File(path).writeAsString(body);
      }
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
            content: Text(
                'wallets.json saved. Anyone with this file can spend your ANM — keep it safe.')),
      );
    } catch (e) {
      // No document provider / dialog unavailable — offer the share sheet
      // instead so the export still succeeds.
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Save failed ($e) — opening Share instead.')),
      );
      await _shareExport(context, body);
    }
  }

  Future<void> _shareExport(BuildContext context, String body) async {
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
      builder: (c) => StatefulBuilder(
        builder: (c, setLocal) {
          // A full wallets.json (many ML-DSA-65 keys) is large and often a
          // single minified line — the field must accept UNLIMITED text.
          // maxLines:null + a fixed-height scroll view takes any size and
          // scrolls; no maxLength/formatter caps input. The "Paste from
          // clipboard" button reads the whole clipboard directly, which is the
          // most reliable way to get a big blob in on Android regardless of
          // any long-press-paste quirk.
          return AlertDialog(
            title: const Text('Paste wallets.json'),
            content: SizedBox(
              width: double.maxFinite,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 320),
                    child: Scrollbar(
                      thumbVisibility: true,
                      child: TextField(
                        controller: ctrl,
                        // Unbounded: accept any number of lines / characters.
                        maxLines: null,
                        minLines: 8,
                        expands: false,
                        keyboardType: TextInputType.multiline,
                        textInputAction: TextInputAction.newline,
                        autocorrect: false,
                        enableSuggestions: false,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          hintText: '{"wallets": [...]}',
                        ),
                        onChanged: (_) => setLocal(() {}),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      TextButton.icon(
                        icon: const Icon(Icons.content_paste, size: 18),
                        label: const Text('Paste from clipboard'),
                        onPressed: () async {
                          final data = await Clipboard.getData(Clipboard.kTextPlain);
                          final t = data?.text ?? '';
                          if (t.isNotEmpty) {
                            // Append rather than replace so a two-part paste
                            // still works; users almost always start empty.
                            ctrl.text = ctrl.text.isEmpty ? t : ctrl.text + t;
                            ctrl.selection = TextSelection.collapsed(offset: ctrl.text.length);
                            setLocal(() {});
                          }
                        },
                      ),
                      const Spacer(),
                      Text('${ctrl.text.length} chars',
                          style: TextStyle(
                              fontSize: 11,
                              color: Theme.of(c).colorScheme.outline)),
                      if (ctrl.text.isNotEmpty)
                        IconButton(
                          tooltip: 'Clear',
                          icon: const Icon(Icons.clear, size: 18),
                          onPressed: () => setLocal(() => ctrl.clear()),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
              FilledButton(
                  onPressed: () => Navigator.pop(c, ctrl.text),
                  child: const Text('Import')),
            ],
          );
        },
      ),
    );
  }
}

/// Enable/disable fingerprint / Face ID unlock. Enabling captures the current
/// (unlocked) key behind a biometric prompt; the toggle hides itself on devices
/// without biometric hardware/enrollment.
class _BiometricToggle extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider).value;
    if (auth == null || !auth.biometricAvailable) return const SizedBox.shrink();
    return SwitchListTile(
      secondary: const Icon(Icons.fingerprint),
      title: const Text('Biometric unlock'),
      subtitle: Text(auth.biometricEnabled
          ? 'Unlock with fingerprint or Face ID. Password still works.'
          : 'Use fingerprint or Face ID instead of typing your password.'),
      value: auth.biometricEnabled,
      onChanged: (want) async {
        final notifier = ref.read(authProvider.notifier);
        if (want) {
          if (!auth.unlocked) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                content: Text('Unlock the wallet first, then enable biometrics.')));
            return;
          }
          final ok = await notifier.enableBiometric();
          if (!context.mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(ok
                  ? 'Biometric unlock enabled.'
                  : 'Could not enable biometric unlock.')));
        } else {
          await notifier.disableBiometric();
          if (!context.mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Biometric unlock disabled.')));
        }
      },
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
