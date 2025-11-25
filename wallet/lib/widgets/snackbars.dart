import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Lightweight helpers to show consistent SnackBars across the app.
///
/// Usage:
/// ```dart
/// showSuccessSnack(context, 'Transaction sent!');
/// showErrorSnack(context, 'Failed to send');
/// showInfoSnack(context, 'Copied to clipboard');
/// showTxSubmittedSnack(context, txHash: '0xabc...', onView: () { /* open explorer */ });
/// ```
///
/// You can also build your own:
/// ```dart
/// showAppSnack(
///   context,
///   'Custom message',
///   type: SnackType.warning,
///   actionLabel: 'UNDO',
///   onAction: () { /* ... */ },
/// );
/// ```

enum SnackType { info, success, warning, error }

/// Show a themed SnackBar with optional action.
void showAppSnack(
  BuildContext context,
  String message, {
  SnackType type = SnackType.info,
  String? actionLabel,
  VoidCallback? onAction,
  Duration duration = const Duration(seconds: 3),
  IconData? leadingIcon,
}) {
  final theme = Theme.of(context);
  final cs = theme.colorScheme;

  // Resolve colors & default icon by type
  (Color bg, Color fg, IconData icon) palette(SnackType t) {
    switch (t) {
      case SnackType.success:
        return (
          cs.secondaryContainer,
          cs.onSecondaryContainer,
          Icons.check_circle_rounded
        );
      case SnackType.warning:
        return (cs.tertiaryContainer, cs.onTertiaryContainer, Icons.warning_rounded);
      case SnackType.error:
        return (cs.errorContainer, cs.onErrorContainer, Icons.error_outline_rounded);
      case SnackType.info:
      default:
        return (cs.surfaceContainerHigh, cs.onSurface, Icons.info_outline_rounded);
    }
  }

  final (bg, fg, icon) = palette(type);
  final iconToUse = leadingIcon ?? icon;

  // Replace any current snack to avoid stacking.
  final sm = ScaffoldMessenger.of(context);
  sm.hideCurrentSnackBar();

  final snack = SnackBar(
    behavior: SnackBarBehavior.floating,
    duration: duration,
    elevation: 0,
    backgroundColor: bg,
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    content: Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(iconToUse, color: fg),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            message,
            style: theme.textTheme.bodyMedium?.copyWith(color: fg),
          ),
        ),
      ],
    ),
    action: (actionLabel != null && onAction != null)
        ? SnackBarAction(
            label: actionLabel,
            textColor: fg,
            onPressed: onAction,
          )
        : null,
  );

  sm.showSnackBar(snack);
}

void showSuccessSnack(BuildContext context, String message,
        {Duration duration = const Duration(seconds: 3)}) =>
    showAppSnack(context, message, type: SnackType.success, duration: duration);

void showErrorSnack(BuildContext context, String message,
        {Duration duration = const Duration(seconds: 4)}) =>
    showAppSnack(context, message, type: SnackType.error, duration: duration);

void showWarningSnack(BuildContext context, String message,
        {Duration duration = const Duration(seconds: 4)}) =>
    showAppSnack(context, message, type: SnackType.warning, duration: duration);

void showInfoSnack(BuildContext context, String message,
        {Duration duration = const Duration(seconds: 3)}) =>
    showAppSnack(context, message, type: SnackType.info, duration: duration);

/// Quick helper for "Copied" UX.
/// If [copy] is provided, it will be placed on the clipboard before showing the snack.
Future<void> showCopiedSnack(
  BuildContext context, {
  String label = 'Copied to clipboard',
  String? copy,
}) async {
  if (copy != null && copy.isNotEmpty) {
    await Clipboard.setData(ClipboardData(text: copy));
  }
  showSuccessSnack(context, label);
}

/// Show a "Transaction submitted" snack with copy & optional "VIEW" action.
///
/// If [onView] is provided, a "VIEW" action button appears.
/// Long-press or tap the body to copy the hash (we also expose a tiny copy icon).
void showTxSubmittedSnack(
  BuildContext context, {
  required String txHash,
  VoidCallback? onView,
}) {
  final theme = Theme.of(context);
  final cs = theme.colorScheme;

  final sm = ScaffoldMessenger.of(context);
  sm.hideCurrentSnackBar();

  final fg = cs.onSecondaryContainer;
  final snack = SnackBar(
    behavior: SnackBarBehavior.floating,
    duration: const Duration(seconds: 6),
    elevation: 0,
    backgroundColor: cs.secondaryContainer,
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    content: InkWell(
      onLongPress: () async {
        await Clipboard.setData(ClipboardData(text: txHash));
        showInfoSnack(context, 'Hash copied');
      },
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Icon(Icons.check_circle_rounded, color: fg),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Transaction submitted', style: theme.textTheme.bodyMedium?.copyWith(color: fg, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        _shortHash(txHash),
                        style: theme.textTheme.bodySmall?.copyWith(color: fg.withOpacity(0.9)),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    IconButton(
                      tooltip: 'Copy hash',
                      icon: Icon(Icons.copy, size: 18, color: fg),
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: txHash));
                        showInfoSnack(context, 'Hash copied');
                      },
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    ),
    action: onView != null
        ? SnackBarAction(
            label: 'VIEW',
            textColor: fg,
            onPressed: onView,
          )
        : null,
  );

  sm.showSnackBar(snack);
}

/// Provide a consistent SnackBar theme for the app (optional).
SnackBarThemeData buildAppSnackBarTheme(ColorScheme cs, TextTheme tt) {
  return SnackBarThemeData(
    behavior: SnackBarBehavior.floating,
    backgroundColor: cs.surfaceContainerHigh,
    contentTextStyle: tt.bodyMedium?.copyWith(color: cs.onSurface),
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    elevation: 0,
  );
}

// ----------------- internals -----------------

String _shortHash(String h) {
  final t = h.trim();
  if (t.length <= 18) return t;
  return '${t.substring(0, 10)}…${t.substring(t.length - 8)}';
}
