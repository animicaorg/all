import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// AmountField — numeric input with decimal support, validation and a "MAX" helper.
///
/// Features
/// • Restricts input to digits and a single decimal separator.
/// • Configurable [decimals] (default 18).
/// • Optional balance-aware validation and "MAX" button (via [availableUnits]).
/// • Emits parsed minimal-units (BigInt-like encoded as String) via [onAmountUnits].
///
/// Minimal-units format
/// • We represent minimal units as a decimal string (e.g. "1000000000000000000" for 1.0 with 18 decimals)
///   to avoid BigInt allocation in the widget. Consumers can parse to BigInt if desired.
///
/// Usage
/// ```dart
/// final controller = TextEditingController();
/// AmountField(
///   controller: controller,
///   symbol: 'ANM',
///   decimals: 18,
///   availableUnits: '1234500000000000000', // 1.2345 ANM (optional)
///   onAmountUnits: (u) { /* u is null or a decimal-string minimal-units */ },
/// )
/// ```
class AmountField extends StatefulWidget {
  const AmountField({
    super.key,
    this.controller,
    this.labelText = 'Amount',
    this.hintText = '0.0',
    this.symbol = 'ANM',
    this.decimals = 18,
    this.enabled = true,
    this.autofocus = false,
    this.availableUnits, // decimal-string minimal units
    this.minUnits,       // decimal-string minimal units
    this.maxUnits,       // decimal-string minimal units (overrides available)
    this.onChanged,
    this.onSubmitted,
    this.onAmountUnits,  // parsed minimal-units string callback
    this.showMaxButton = true,
  });

  final TextEditingController? controller;
  final String labelText;
  final String hintText;
  final String symbol;
  final int decimals;
  final bool enabled;
  final bool autofocus;

  /// Decimal-string minimal units for available balance (e.g. "1000000000000000000").
  final String? availableUnits;

  /// Decimal-string minimal units for minimum amount (optional).
  final String? minUnits;

  /// Decimal-string minimal units for maximum amount (optional).
  final String? maxUnits;

  /// Raw text change (already normalized).
  final ValueChanged<String>? onChanged;

  /// Raw text submitted (keyboard done).
  final ValueChanged<String>? onSubmitted;

  /// Callback with parsed minimal units; null if invalid/empty.
  final ValueChanged<String?>? onAmountUnits;

  /// Show MAX button when [availableUnits] is provided.
  final bool showMaxButton;

  @override
  State<AmountField> createState() => _AmountFieldState();
}

class _AmountFieldState extends State<AmountField> {
  late final TextEditingController _controller =
      widget.controller ?? TextEditingController();
  late final bool _ownsController = widget.controller == null;

  String? _error;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onText);
  }

  @override
  void dispose() {
    _controller.removeListener(_onText);
    if (_ownsController) _controller.dispose();
    super.dispose();
  }

  void _onText() {
    widget.onChanged?.call(_controller.text);
    final parsed = _parseToUnits(_controller.text, widget.decimals);
    widget.onAmountUnits?.call(parsed);
    setState(() {
      _error = _validate(_controller.text, parsed);
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final availablePretty = _prettyUnits(widget.availableUnits, widget.decimals);
    final hasAvail = widget.availableUnits != null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TextFormField(
          controller: _controller,
          enabled: widget.enabled,
          autofocus: widget.autofocus,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.done,
          inputFormatters: [
            FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
            _DecimalGuardFormatter(maxDecimals: widget.decimals),
            _LeadingDotToZeroFormatter(),
          ],
          decoration: InputDecoration(
            labelText: widget.labelText,
            hintText: widget.hintText,
            errorText: _error,
            suffixIcon: _Suffix(
              symbol: widget.symbol,
              showMax: widget.showMaxButton && hasAvail,
              onMax: (widget.availableUnits != null)
                  ? () => _setToUnits(widget.availableUnits!)
                  : null,
            ),
          ),
          onFieldSubmitted: (v) => widget.onSubmitted?.call(v),
          style: theme.textTheme.bodyLarge,
        ),
        if (hasAvail)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              'Available: $availablePretty ${widget.symbol}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.textTheme.bodySmall?.color?.withOpacity(0.7),
              ),
            ),
          ),
      ],
    );
  }

  // ----- Formatting / parsing helpers -----

  /// Convert user-facing decimal string to minimal-units decimal-string.
  /// Returns null if invalid/empty.
  String? _parseToUnits(String input, int decimals) {
    final t = input.trim();
    if (t.isEmpty) return null;
    if (!_isDecimal(t)) return null;

    final parts = t.split('.');
    final intPart = parts[0].isEmpty ? '0' : parts[0];
    final fracPart = parts.length > 1 ? parts[1] : '';
    if (fracPart.length > decimals) return null;

    final fracPadded = fracPart.padRight(decimals, '0');
    final combined = _stripLeadingZeros('$intPart$fracPadded');
    return combined.isEmpty ? '0' : combined;
  }

  /// Pretty-print minimal-units string using [decimals].
  String _prettyUnits(String? units, int decimals) {
    if (units == null || units.isEmpty) return '0';
    final u = _stripLeadingZeros(units);
    final negative = u.startsWith('-');
    final pos = negative ? u.substring(1) : u;

    if (decimals <= 0) return negative ? '-$pos' : pos;

    final len = pos.length;
    final wholeLen = (len - decimals).clamp(0, len);
    final whole = wholeLen == 0 ? '0' : pos.substring(0, wholeLen);
    final frac = pos.substring(wholeLen).padLeft(decimals, '0');
    // trim trailing zeros in frac (but keep at least one digit)
    final trimmedFrac = frac.replaceFirst(RegExp(r'0+$'), '');
    final body = trimmedFrac.isEmpty ? whole : '$whole.$trimmedFrac';
    return negative ? '-$body' : body;
  }

  bool _isDecimal(String s) {
    // Allow "0", "0.", ".5", "10.123"
    final re = RegExp(r'^\d*\.?\d*$');
    if (!re.hasMatch(s)) return false;
    // Disallow just "."
    if (s == '.') return false;
    // Disallow leading zeros like "00" unless "0" or "0.xxx"
    if (s.startsWith('00')) return false;
    return true;
  }

  String _stripLeadingZeros(String s) {
    final neg = s.startsWith('-');
    var t = neg ? s.substring(1) : s;
    t = t.replaceFirst(RegExp(r'^0+'), '');
    if (t.isEmpty) t = '0';
    return neg ? '-$t' : t;
  }

  // ----- Validation -----

  String? _validate(String raw, String? units) {
    final s = raw.trim();
    if (s.isEmpty) return 'Amount is required';
    if (!_isDecimal(s)) return 'Invalid number';
    if (s == '.' || s == '0.' || s == '0') return 'Amount must be greater than zero';

    if (units == null) return 'Invalid amount';

    // Min/Max/Available comparisons
    final cmpMin = widget.minUnits;
    if (cmpMin != null && _cmpUnits(units, cmpMin) < 0) {
      return 'Below minimum';
    }

    final max = widget.maxUnits ?? widget.availableUnits;
    if (max != null && _cmpUnits(units, max) > 0) {
      return 'Exceeds available';
    }
    return null;
  }

  /// Lexicographic-like comparison for non-negative decimal-strings (no leading zeros).
  /// Returns -1, 0, 1.
  int _cmpUnits(String a, String b) {
    final aa = _stripLeadingZeros(a);
    final bb = _stripLeadingZeros(b);
    if (aa.length != bb.length) return aa.length < bb.length ? -1 : 1;
    if (aa == bb) return 0;
    return aa.compareTo(bb) < 0 ? -1 : 1;
    // Note: both assumed non-negative here for wallet UI.
  }

  void _setToUnits(String units) {
    final pretty = _prettyUnits(units, widget.decimals);
    _controller.text = pretty;
    _controller.selection = TextSelection.collapsed(offset: pretty.length);
  }
}

/// Suffix area with token symbol and optional MAX button.
class _Suffix extends StatelessWidget {
  const _Suffix({
    required this.symbol,
    required this.showMax,
    required this.onMax,
  });

  final String symbol;
  final bool showMax;
  final VoidCallback? onMax;

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[
      Padding(
        padding: const EdgeInsets.only(right: 6.0),
        child: Text(
          symbol,
          style: Theme.of(context).textTheme.labelLarge,
        ),
      ),
    ];
    if (showMax && onMax != null) {
      children.add(
        TextButton(
          onPressed: onMax,
          child: const Text('MAX'),
        ),
      );
    }
    return Row(mainAxisSize: MainAxisSize.min, children: children);
  }
}

/// Ensures at most one '.' and limits fractional digits to [maxDecimals].
class _DecimalGuardFormatter extends TextInputFormatter {
  _DecimalGuardFormatter({required this.maxDecimals});
  final int maxDecimals;

  @override
  TextEditingValue formatEditUpdate(
      TextEditingValue oldValue, TextEditingValue newValue) {
    final t = newValue.text;

    // Only digits and dot allowed (FilteringTextInputFormatter already applied).
    // Enforce single dot:
    final dots = '.'.allMatches(t).length;
    if (dots > 1) return oldValue;

    // Limit fraction length
    final parts = t.split('.');
    if (parts.length == 2 && parts[1].length > maxDecimals) {
      return oldValue;
    }

    // Avoid leading zeros like "00" (allow "0" and "0.xxx")
    if (t.startsWith('00')) return oldValue;

    // Everything else OK.
    return newValue;
  }
}

/// Converts an initial '.' into '0.' for better UX.
class _LeadingDotToZeroFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
      TextEditingValue oldValue, TextEditingValue newValue) {
    final t = newValue.text;
    if (t == '.') {
      return TextEditingValue(
        text: '0.',
        selection: const TextSelection.collapsed(offset: 2),
      );
    }
    return newValue;
  }
}
