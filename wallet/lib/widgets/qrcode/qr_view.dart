import 'package:flutter/foundation.dart' show kIsWeb, defaultTargetPlatform, TargetPlatform;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

/// QR scanner view:
/// - On iOS/Android: uses camera via `mobile_scanner`.
/// - On desktop/web: shows a simple fallback UI to paste the code manually
///   (camera scanning on those targets is not guaranteed/supported).
///
/// Usage:
/// ```dart
/// final result = await showQrScanSheet(context, title: 'Scan address');
/// if (result != null) { /* ... */ }
/// ```
///
/// Dependencies:
///   mobile_scanner: ^6.0.0 (or compatible)
///
/// Notes:
/// • We stop after the first detection unless [continuous] = true.
/// • The fallback lets users paste from clipboard or type manually.
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
  bool _torchOn = false;
  bool _frontCam = false;
  bool _didReturn = false;

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

          if (isMobile) _buildMobileScanner(context) else _buildFallback(context),

          const SizedBox(height: 12),

          if (isMobile)
            Row(
              children: [
                _IconToggle(
                  iconOn: Icons.flash_on,
                  iconOff: Icons.flash_off,
                  label: _torchOn ? 'Torch on' : 'Torch off',
                  value: _torchOn,
                  onChanged: (v) => setState(() => _torchOn = v),
                ),
                const SizedBox(width: 12),
                _IconToggle(
                  iconOn: Icons.cameraswitch,
                  iconOff: Icons.cameraswitch,
                  label: _frontCam ? 'Front' : 'Back',
                  value: _frontCam,
                  onChanged: (v) => setState(() => _frontCam = v),
                ),
                const Spacer(),
                TextButton.icon(
                  onPressed: () {
                    Clipboard.getData('text/plain').then((clip) {
                      final t = clip?.text?.trim();
                      if (t != null && t.isNotEmpty) {
                        _returnOnce(t);
                      } else {
                        _showSnack(context, 'Clipboard is empty');
                      }
                    });
                  },
                  icon: const Icon(Icons.paste),
                  label: const Text('Paste'),
                ),
              ],
            )
          else
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

  Widget _buildMobileScanner(BuildContext context) {
    // We keep a controller inside the widget tree so toggles can call setTorch etc.
    final controller = MobileScannerController(
      torchEnabled: _torchOn,
      facing: _frontCam ? CameraFacing.front : CameraFacing.back,
      detectionSpeed: DetectionSpeed.noDuplicates,
      formats: BarcodeFormat
          .values, // accept any, caller decides how to parse the payload
    );

    // Keep the controller in sync with toggles (rebuilds create a new controller).
    // To avoid leaking camera, rely on MobileScanner disposing when widget unmounts.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      controller.toggleTorch(_torchOn);
      controller.switchCamera(_frontCam ? CameraFacing.front : CameraFacing.back);
    });

    return AspectRatio(
      aspectRatio: 3 / 4,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: Stack(
          fit: StackFit.expand,
          children: [
            MobileScanner(
              controller: controller,
              onDetect: (capture) {
                if (_didReturn) return;
                final codes = capture.barcodes;
                if (codes.isEmpty) return;
                // take the first non-empty raw value
                for (final b in codes) {
                  final raw = b.rawValue?.trim();
                  if (raw != null && raw.isNotEmpty) {
                    if (widget.continuous) {
                      // Bubble the result but keep scanning
                      _showSnack(context, 'Scanned: ${_short(raw)}');
                    } else {
                      _returnOnce(raw);
                    }
                    break;
                  }
                }
              },
            ),
            // Overlay
            _ScannerOverlay(),
          ],
        ),
      ),
    );
  }

  Widget _buildFallback(BuildContext context) {
    final controller = TextEditingController();
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
                'Camera scan is not available on this platform.\n'
                'Paste the code below or type it manually.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: controller,
                decoration: const InputDecoration(
                  labelText: 'QR contents',
                  hintText: 'Paste or type here…',
                ),
                minLines: 1,
                maxLines: 3,
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.paste),
                      label: const Text('Paste'),
                      onPressed: () async {
                        final clip = await Clipboard.getData('text/plain');
                        final t = clip?.text?.trim();
                        if (t != null && t.isNotEmpty) {
                          controller.text = t;
                        } else {
                          _showSnack(context, 'Clipboard is empty');
                        }
                      },
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.check),
                      label: const Text('Use value'),
                      onPressed: () {
                        final t = controller.text.trim();
                        if (t.isEmpty) {
                          _showSnack(context, 'Please enter a value');
                        } else {
                          _returnOnce(t);
                        }
                      },
                    ),
                  ),
                ],
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

  void _showSnack(BuildContext context, String msg) {
    final sm = ScaffoldMessenger.of(context);
    sm.hideCurrentSnackBar();
    sm.showSnackBar(
      SnackBar(
        behavior: SnackBarBehavior.floating,
        content: Text(msg),
      ),
    );
  }

  String _short(String s) {
    if (s.length <= 24) return s;
    return '${s.substring(0, 12)}…${s.substring(s.length - 10)}';
  }
}

/// A soft overlay with a rounded scanning window & corner guides.
class _ScannerOverlay extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _ScannerOverlayPainter(
        borderColor: Theme.of(context).colorScheme.primary,
      ),
    );
  }
}

class _ScannerOverlayPainter extends CustomPainter {
  _ScannerOverlayPainter({required this.borderColor});

  final Color borderColor;

  @override
  void paint(Canvas canvas, Size size) {
    final overlayPaint = Paint()
      ..color = Colors.black.withOpacity(0.35)
      ..style = PaintingStyle.fill;

    final holeSize = Size(size.width * 0.75, size.height * 0.42);
    final holeRect = Rect.fromCenter(
      center: Offset(size.width / 2, size.height / 2),
      width: holeSize.width,
      height: holeSize.height,
    );

    final rrect = RRect.fromRectAndRadius(holeRect, const Radius.circular(18));

    // Darken around the hole
    final path = Path()..addRect(Offset.zero & size);
    final holePath = Path()..addRRect(rrect);
    final overlayPath = Path.combine(PathOperation.difference, path, holePath);
    canvas.drawPath(overlayPath, overlayPaint);

    // Corner guides
    final guide = Paint()
      ..color = borderColor
      ..strokeWidth = 4
      ..style = PaintingStyle.stroke;
    final corner = 22.0;

    // top-left
    canvas.drawLine(holeRect.topLeft, holeRect.topLeft + Offset(corner, 0), guide);
    canvas.drawLine(holeRect.topLeft, holeRect.topLeft + Offset(0, corner), guide);
    // top-right
    canvas.drawLine(holeRect.topRight, holeRect.topRight + Offset(-corner, 0), guide);
    canvas.drawLine(holeRect.topRight, holeRect.topRight + Offset(0, corner), guide);
    // bottom-left
    canvas.drawLine(holeRect.bottomLeft, holeRect.bottomLeft + Offset(corner, 0), guide);
    canvas.drawLine(holeRect.bottomLeft, holeRect.bottomLeft + Offset(0, -corner), guide);
    // bottom-right
    canvas.drawLine(holeRect.bottomRight, holeRect.bottomRight + Offset(-corner, 0), guide);
    canvas.drawLine(holeRect.bottomRight, holeRect.bottomRight + Offset(0, -corner), guide);
  }

  @override
  bool shouldRepaint(covariant _ScannerOverlayPainter oldDelegate) {
    return oldDelegate.borderColor != borderColor;
  }
}

/// Small pill toggle used for torch/camera controls.
class _IconToggle extends StatelessWidget {
  const _IconToggle({
    required this.iconOn,
    required this.iconOff,
    required this.label,
    required this.value,
    required this.onChanged,
  });

  final IconData iconOn;
  final IconData iconOff;
  final String label;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      borderRadius: BorderRadius.circular(999),
      onTap: () => onChanged(!value),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: cs.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: cs.outlineVariant.withOpacity(0.6)),
        ),
        child: Row(
          children: [
            Icon(value ? iconOn : iconOff, size: 18),
            const SizedBox(width: 6),
            Text(label),
          ],
        ),
      ),
    );
  }
}
