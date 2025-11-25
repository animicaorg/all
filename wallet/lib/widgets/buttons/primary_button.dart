import 'package:flutter/material.dart';

/// PrimaryButton — a flexible, app-wide CTA button.
///
/// Features:
/// • Primary / Tonal / Danger tones
/// • Small / Normal / Large sizes
/// • Loading state with spinner
/// • Optional leading icon
/// • Full-width (expand) layout
///
/// Example:
/// ```dart
/// PrimaryButton(
///   label: 'Send',
///   tone: ButtonTone.primary,
///   size: ButtonSize.normal,
///   loading: isSubmitting,
///   onPressed: isSubmitting ? null : _submit,
/// )
/// ```
class PrimaryButton extends StatelessWidget {
  const PrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.tone = ButtonTone.primary,
    this.size = ButtonSize.normal,
    this.loading = false,
    this.expand = true,
    this.icon,
  }) : assert(label.length > 0, 'label cannot be empty');

  final String label;
  final VoidCallback? onPressed;
  final ButtonTone tone;
  final ButtonSize size;
  final bool loading;
  final bool expand;
  final Widget? icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    final enabled = onPressed != null && !loading;

    // Resolve colors by tone
    final (bg, fg, overlay) = switch (tone) {
      ButtonTone.primary => (cs.primary, cs.onPrimary, cs.primary.withOpacity(0.12)),
      ButtonTone.tonal   => (cs.secondaryContainer, cs.onSecondaryContainer, cs.secondary.withOpacity(0.08)),
      ButtonTone.danger  => (cs.error, cs.onError, cs.error.withOpacity(0.12)),
    };

    final style = ButtonStyle(
      minimumSize: MaterialStateProperty.all(_minSizeFor(size)),
      padding: MaterialStateProperty.all(_paddingFor(size)),
      shape: MaterialStateProperty.all(
        RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      elevation: MaterialStateProperty.resolveWith((states) {
        if (states.contains(MaterialState.disabled)) return 0;
        if (states.contains(MaterialState.pressed)) return 1;
        return 0;
      }),
      backgroundColor: MaterialStateProperty.resolveWith((states) {
        if (states.contains(MaterialState.disabled)) {
          return theme.disabledColor.withOpacity(0.12);
        }
        if (states.contains(MaterialState.pressed) ||
            states.contains(MaterialState.hovered) ||
            states.contains(MaterialState.focused)) {
          return _blend(bg, overlay);
        }
        return bg;
      }),
      foregroundColor: MaterialStateProperty.resolveWith((states) {
        if (states.contains(MaterialState.disabled)) {
          return theme.disabledColor.withOpacity(0.38);
        }
        return fg;
      }),
      overlayColor: MaterialStateProperty.all(overlay),
      animationDuration: const Duration(milliseconds: 120),
      enableFeedback: true,
      splashFactory: InkRipple.splashFactory,
    );

    final child = _ButtonContents(
      label: label,
      icon: icon,
      loading: loading,
      textStyle: _textStyleFor(theme, size),
      spinnerColor: fg,
      gap: _gapFor(size),
    );

    final btn = ElevatedButton(
      style: style,
      onPressed: enabled ? onPressed : null,
      child: child,
    );

    if (expand) {
      return ConstrainedBox(
        constraints: const BoxConstraints(minWidth: double.infinity),
        child: btn,
      );
    }
    return btn;
  }

  // ----- sizing -----
  Size _minSizeFor(ButtonSize s) => switch (s) {
        ButtonSize.small => const Size(64, 36),
        ButtonSize.normal => const Size(88, 44),
        ButtonSize.large => const Size(96, 52),
      };

  EdgeInsets _paddingFor(ButtonSize s) => switch (s) {
        ButtonSize.small => const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        ButtonSize.normal => const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ButtonSize.large => const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      };

  TextStyle _textStyleFor(ThemeData t, ButtonSize s) => switch (s) {
        ButtonSize.small => t.textTheme.labelLarge ?? const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        ButtonSize.normal => t.textTheme.titleSmall ?? const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ButtonSize.large => t.textTheme.titleMedium ?? const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
      };

  double _gapFor(ButtonSize s) => switch (s) {
        ButtonSize.small => 8,
        ButtonSize.normal => 10,
        ButtonSize.large => 12,
      };

  // Slightly blend a base color with an overlay tint for hover/press
  Color _blend(Color base, Color overlay) {
    // Simple alpha composition
    final a = overlay.opacity;
    return Color.alphaBlend(overlay, base.withOpacity(1.0)).withOpacity(1.0 - (1.0 - base.opacity) * (1.0 - a));
  }
}

enum ButtonTone { primary, tonal, danger }
enum ButtonSize { small, normal, large }

class _ButtonContents extends StatelessWidget {
  const _ButtonContents({
    required this.label,
    required this.textStyle,
    required this.spinnerColor,
    required this.gap,
    this.icon,
    this.loading = false,
  });

  final String label;
  final TextStyle textStyle;
  final Color spinnerColor;
  final double gap;
  final Widget? icon;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[];

    if (loading) {
      children.add(SizedBox(
        width: _spinnerSize(textStyle),
        height: _spinnerSize(textStyle),
        child: CircularProgressIndicator(
          strokeWidth: 2,
          valueColor: AlwaysStoppedAnimation<Color>(spinnerColor),
        ),
      ));
    } else if (icon != null) {
      children.add(IconTheme(
        data: IconThemeData(size: _iconSize(textStyle)),
        child: icon!,
      ));
    }

    if (children.isNotEmpty) {
      children.add(SizedBox(width: gap));
    }

    children.add(Flexible(
      fit: FlexFit.loose,
      child: Text(
        label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: textStyle,
      ),
    ));

    return Row(
      mainAxisSize: MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: children,
    );
  }

  double _iconSize(TextStyle s) => switch (s.fontSize?.round()) {
        <= 14 => 16,
        <= 16 => 18,
        _ => 20,
      }.toDouble();

  double _spinnerSize(TextStyle s) => switch (s.fontSize?.round()) {
        <= 14 => 16,
        <= 16 => 18,
        _ => 20,
      }.toDouble();
}
