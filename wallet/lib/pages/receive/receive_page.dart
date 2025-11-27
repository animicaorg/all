import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../state/account_state.dart';
import '../../utils/format.dart';
import '../common/placeholder_page.dart';

class ReceivePage extends ConsumerWidget {
  const ReceivePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeAccountProvider);
    if (active == null) {
      return const PlaceholderPage(
        title: 'Receive',
        icon: Icons.qr_code_2_outlined,
        message: 'Add or select an account to receive funds.',
      );
    }

    final addr = active.address;
    final label = active.label.isEmpty ? shortAddress(addr) : active.label;

    return Scaffold(
      appBar: AppBar(title: const Text('Receive')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Show this code to receive funds',
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 16),
              DecoratedBox(
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surface,
                  borderRadius: BorderRadius.circular(16),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.08),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ],
                ),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: QrImageView(
                    data: addr,
                    size: 220,
                    eyeStyle: const QrEyeStyle(eyeShape: QrEyeShape.square),
                    dataModuleStyle: const QrDataModuleStyle(
                      dataModuleShape: QrDataModuleShape.square,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Text(label, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 6),
              SelectableText(addr,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyMedium),
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: addr));
                  if (context.mounted) {
                    ScaffoldMessenger.of(context)
                        .showSnackBar(const SnackBar(content: Text('Address copied')));
                  }
                },
                icon: const Icon(Icons.copy),
                label: const Text('Copy address'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
