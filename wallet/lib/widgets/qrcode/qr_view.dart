import 'package:flutter/foundation.dart' show kIsWeb, defaultTargetPlatform, TargetPlatform;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// QR scanner view:
/// - On iOS/Android: camera scanning is currently disabled to keep the web
///   build light; we expose a consistent fallback UX for all platforms.
/// - On desktop/web: shows a simple fallback UI to paste the code manually.
///
/// Usage:
/// ```dart
/// final result = await showQrScanSheet(context, title: 'Scan address');
/// if (result != null) { /* ... */ }
/// ```
Future<String?> showQrScanSheet(
  BuildContext context, {
  String title = 'Scan QR Code',
  bool continuous = false,
}) {
  return showModalBottomSheet<String>(
    context: context,
    useSafeArea: true,
    isScrollControlled: true,
    backgroundColor: Theme.of(context).colorScheme.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (ctx) => _QrSheet(title: title, continuous: continuous),
  );
}

class _QrSheet extends StatefulWidget {
  const _QrSheet({required this.title, required this.continuous});
  final String title;
  final bool continuous;

  @override
  State<_QrSheet> createState() => _QrSheetState();
}

class _QrSheetState extends State<_QrSheet> {
  final TextEditingController _textCtrl = TextEditingController();
  bool _didReturn = false;

  @override
  void dispose() {
    _textCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isMobile = !kIsWeb &&
        (defaultTargetPlatform == TargetPlatform.iOS ||
            defaultTargetPlatform == TargetPlatform.android);

    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 12,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // grab handle
          Container(
            width: 44,
            height: 5,
            margin: const EdgeInsets.only(bottom: 12),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.outlineVariant,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          Row(
            children: [
              Expanded(
                child: Text(widget.title,
                    style: Theme.of(context).textTheme.titleLarge),
              ),
              IconButton(
                tooltip: 'Close',
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.of(context).pop(),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _buildFallback(context, isMobile: isMobile),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: () async {
                final clip = await Clipboard.getData('text/plain');
                final t = clip?.text?.trim();
                if (t != null && t.isNotEmpty) {
                  _returnOnce(t);
                } else {
                  _showSnack(context, 'Clipboard is empty');
                }
              },
              icon: const Icon(Icons.paste),
              label: const Text('Paste'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFallback(BuildContext context, {required bool isMobile}) {
    final platformLabel = isMobile ? 'mobile' : 'desktop/web';
    return Column(
      children: [
        Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: Theme.of(context).colorScheme.outlineVariant.withOpacity(0.7),
            ),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
          ),
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              const Icon(Icons.qr_code_2, size: 48),
              const SizedBox(height: 8),
              Text(
                'Camera scanning is disabled for this $platformLabel build.\n'
                'Paste the code below or type it manually.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _textCtrl,
                decoration: const InputDecoration(
                  labelText: 'QR contents',
                  hintText: 'Paste or type here…',
                ),
                minLines: 1,
                maxLines: 3,
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: () {
                  final t = _textCtrl.text.trim();
                  if (t.isEmpty) {
                    _showSnack(context, 'Enter or paste a value first');
                    return;
                  }
                  if (widget.continuous) {
                    _showSnack(context, 'Captured: ${_short(t)}');
                  } else {
                    _returnOnce(t);
                  }
                },
                icon: const Icon(Icons.check_circle_outline),
                label: const Text('Use value'),
              ),
            ],
          ),
        ),
      ],
    );
  }

  void _returnOnce(String value) {
    if (_didReturn) return;
    _didReturn = true;
    Navigator.of(context).pop(value);
  }
}

String _short(String s) {
  if (s.length <= 24) return s;
  return '${s.substring(0, 10)}…${s.substring(s.length - 10)}';
}

void _showSnack(BuildContext context, String msg) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(msg)),
  );
}
