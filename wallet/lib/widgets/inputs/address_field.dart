import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// A reusable address input with:
///  • live validation for Animica bech32m (am1...) and 0x-hex addresses
///  • quick actions: Paste / (optional) Scan / Clear
///  • normalization to lowercase (keeps `0x` prefix, keeps `am` HRP)
///
/// Usage:
/// ```dart
/// final controller = TextEditingController();
/// AddressField(
///   controller: controller,
///   onScan: () async {
///     // Present your QR scanner, return a scanned string (or null).
///     // Then: controller.text = scanned;
///   },
/// )
/// ```
class AddressField extends StatefulWidget {
  const AddressField({
    super.key,
    this.controller,
    this.labelText = 'Address',
    this.hintText = 'am1… or 0x…',
    this.autofocus = false,
    this.enabled = true,
    this.onChanged,
    this.onSubmitted,
    this.validator, // optional custom validator (String? -> String? error)
    this.onScan,    // optional: show Scan button if provided
  });

  final TextEditingController? controller;
  final String labelText;
  final String hintText;
  final bool autofocus;
  final bool enabled;

  /// Called when text changes (after normalization).
  final ValueChanged<String>? onChanged;

  /// Called when user submits (keyboard done).
  final ValueChanged<String>? onSubmitted;

  /// Custom validator; if null, a default address validator is used.
  final String? Function(String? value)? validator;

  /// Optional QR scan handler; provide this to show a Scan action button.
  final Future<String?> Function()? onScan;

  @override
  State<AddressField> createState() => _AddressFieldState();
}

class _AddressFieldState extends State<AddressField> {
  late final TextEditingController _controller =
      widget.controller ?? TextEditingController();
  late bool _ownsController = widget.controller == null;

  String? _lastError;
  bool _obscureErrorsUntilDirty = true;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_handleChange);
  }

  @override
  void dispose() {
    _controller.removeListener(_handleChange);
    if (_ownsController) _controller.dispose();
    super.dispose();
  }

  void _handleChange() {
    final norm = _normalize(_controller.text);
    if (norm != _controller.text) {
      final sel = _controller.selection;
      _controller.value = TextEditingValue(
        text: norm,
        selection: sel.copyWith(
          baseOffset: norm.length.clamp(0, norm.length),
          extentOffset: norm.length.clamp(0, norm.length),
        ),
      );
    }
    widget.onChanged?.call(norm);

    if (_obscureErrorsUntilDirty) {
      setState(() {
        _lastError = null;
      });
    } else {
      // update error live
      setState(() {
        _lastError = _validate(norm);
      });
    }
  }

  String _normalize(String s) {
    final t = s.trim();
    if (t.isEmpty) return t;
    if (t.startsWith('0x') || t.startsWith('0X')) {
      return '0x${t.substring(2).toLowerCase()}';
    }
    if (t.startsWith('AM') || t.startsWith('am')) {
      return 'am${t.substring(2)}'.toLowerCase();
    }
    return t;
  }

  String? _defaultValidator(String? v) {
    final s = (v ?? '').trim();
    if (s.isEmpty) return 'Address is required';
    if (_isHexAddr(s)) return null;
    if (_isBech32Animica(s)) return null;
    return 'Invalid Animica address';
  }

  String? _validate(String? v) {
    if (widget.validator != null) return widget.validator!(v);
    return _defaultValidator(v);
  }

  // Rough 0x hex address check (EVM-like), 20+ bytes just in case future proofs.
  bool _isHexAddr(String s) {
    if (s.length < 42) return false;
    final re = RegExp(r'^0x[0-9a-f]{40,}$');
    return re.hasMatch(s);
  }

  // Relaxed bech32m check for HRP "am" (Animica):
  //  - must start with "am1"
  //  - charset: bech32 (no 1, b, i, o) after the separator
  //  - length within sensible range
  bool _isBech32Animica(String s) {
    if (!s.startsWith('am1')) return false;
    if (s.length < 20 || s.length > 120) return false;
    // Bech32 charset (lowercase)
    const charset = '023456789acdefghjklmnpqrstuvwxyz';
    final after = s.substring(3);
    for (final r in after.runes) {
      final ch = String.fromCharCode(r);
      if (!charset.contains(ch)) return false;
    }
    return true;
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text ?? '';
    if (text.isEmpty) return;
    final norm = _normalize(text);
    _controller.text = norm;
  }

  Future<void> _scan() async {
    if (widget.onScan == null) return;
    final scanned = await widget.onScan!.call();
    if (scanned == null || scanned.isEmpty) return;
    // Some QR payloads may be URIs like animica:am1…?amount=…
    final addr = _extractAddressFromPayload(scanned);
    if (addr != null && addr.isNotEmpty) {
      _controller.text = _normalize(addr);
    }
  }

  String? _extractAddressFromPayload(String payload) {
    final p = payload.trim();
    if (p.startsWith('am1')) return p;
    if (p.startsWith('0x') || p.startsWith('0X')) return p;
    // Try URI format: animica:am1... or animica://am1...
    final uriLike = RegExp(r'(am1[0-9a-z]{10,})', caseSensitive: false);
    final m = uriLike.firstMatch(p.toLowerCase());
    if (m != null) return m.group(1);
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return FocusScope(
      onFocusChange: (hasFocus) {
        if (!hasFocus) {
          // Validate on blur
          setState(() {
            _obscureErrorsUntilDirty = false;
            _lastError = _validate(_controller.text);
          });
        }
      },
      child: TextFormField(
        controller: _controller,
        enabled: widget.enabled,
        autofocus: widget.autofocus,
        autovalidateMode: AutovalidateMode.disabled,
        textInputAction: TextInputAction.done,
        keyboardType: TextInputType.text,
        autocorrect: false,
        enableSuggestions: false,
        style: theme.textTheme.bodyMedium?.copyWith(
          fontFeatures: const [FontFeature.tabularFigures()],
        ),
        decoration: InputDecoration(
          labelText: widget.labelText,
          hintText: widget.hintText,
          errorText: _lastError,
          suffixIcon: _SuffixActions(
            onPaste: _paste,
            onScan: widget.onScan != null ? _scan : null,
            onClear: () => _controller.clear(),
          ),
        ),
        validator: _validate,
        onFieldSubmitted: (v) {
          final norm = _normalize(v);
          widget.onSubmitted?.call(norm);
        },
      ),
    );
  }
}

class _SuffixActions extends StatelessWidget {
  const _SuffixActions({
    required this.onPaste,
    required this.onClear,
    this.onScan,
  });

  final VoidCallback onPaste;
  final VoidCallback onClear;
  final VoidCallback? onScan;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[
      IconButton(
        tooltip: 'Paste',
        icon: const Icon(Icons.content_paste),
        onPressed: onPaste,
      ),
      if (onScan != null)
        IconButton(
          tooltip: 'Scan',
          icon: const Icon(Icons.qr_code_scanner),
          onPressed: onScan,
        ),
      IconButton(
        tooltip: 'Clear',
        icon: const Icon(Icons.clear),
        onPressed: onClear,
      ),
    ];
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: children,
    );
  }
}
