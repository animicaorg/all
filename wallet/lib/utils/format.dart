import 'dart:math' as math show min;
import 'package:intl/intl.dart';

import '../constants.dart';

/// Formatting helpers for addresses, amounts (ANM), fiat, and fees.
/// Pure utils — no side effects.

/// Shorten an Animica address: `am1abcd…wxyz`.
String shortAddress(String address, {int head = 6, int tail = 4}) {
  final a = address.trim();
  if (a.length <= head + tail + 1) return a;
  return '${a.substring(0, head)}…${a.substring(a.length - tail)}';
}

/// Quick heuristic to check an Animica bech32m address.
/// For strict validation, use `crypto/bech32m.dart`.
bool looksLikeAnimicaAddress(String address) {
  return AddressFormat.quickBech32.hasMatch(address.trim());
}

/// Format a BigInt amount (atto-ANM, 18 decimals by default) to a
/// human string with up to [precision] fractional digits.
/// - Thousands separators are applied to the integer part.
/// - Trailing zeros are trimmed by default.
/// - Negative values are supported.
///
/// Examples:
///   formatAmount(BigInt.parse('1230000000000000000')) -> "1.23"
///   formatAmount(BigInt.parse('1000000000000000'), precision: 4) -> "0.0010"
String formatAmount(
  BigInt atto, {
  int decimals = 18,
  int precision = 6,
  bool trimZeros = true,
  bool useGrouping = true,
}) {
  final sign = atto.isNegative ? '-' : '';
  final abs = atto.abs();

  final pow10 = _pow10(decimals);
  final intPart = abs ~/ pow10;
  var frac = (abs % pow10).toString().padLeft(decimals, '0');

  // Round to desired precision (<= decimals)
  precision = math.min(precision, decimals);
  if (precision == 0) {
    // Round to integer
    final shouldCarry = decimals > 0 && (frac.codeUnitAt(0) - 48) >= 5;
    final roundedInt = intPart + (shouldCarry ? BigInt.one : BigInt.zero);
    final intStr = _groupInt(roundedInt.toString(), useGrouping: useGrouping);
    return '$sign$intStr';
  } else {
    // Round the fractional string at [precision]
    final carryAndRounded = _roundFrac(frac, precision);
    final carry = carryAndRounded.$1;
    final fracRounded = carryAndRounded.$2;

    final roundedInt = intPart + (carry ? BigInt.one : BigInt.zero);
    final intStr = _groupInt(roundedInt.toString(), useGrouping: useGrouping);
    var out = '$intStr.${fracRounded}';

    if (trimZeros) {
      out = out.replaceFirst(RegExp(r'\.?0+$'), '');
    }
    return '$sign$out';
  }
}

/// Format amount with symbol suffix, e.g., "1.234 ANM".
String formatAmountWithSymbol(
  BigInt atto, {
  int decimals = 18,
  int precision = 6,
  bool trimZeros = true,
  String symbol = 'ANM',
}) {
  final amt = formatAmount(atto,
      decimals: decimals, precision: precision, trimZeros: trimZeros);
  return '$amt $symbol';
}

/// Parse a user-entered decimal string into atto units (BigInt).
/// Accepts grouping separators and symbol; ignores leading/trailing spaces.
/// Returns null if the string is not a number.
///
/// Behavior:
/// - If more than [decimals] fractional digits are provided, rounds HALF-UP.
BigInt? parseAmountToAtto(
  String input, {
  int decimals = 18,
}) {
  var s = input.trim();
  if (s.isEmpty) return null;

  // Drop common currency symbols and spaces
  s = s.replaceAll(RegExp(r'[,\s\$€£¥₿A-Za-z]'), '');

  // Validate number pattern (optional sign, digits, optional .digits)
  final m = RegExp(r'^([+-])?(\d*)(?:\.(\d*))?$').firstMatch(s);
  if (m == null) return null;

  final signStr = m.group(1) ?? '';
  final intStr = (m.group(2) ?? '').isEmpty ? '0' : m.group(2)!;
  var fracStr = m.group(3) ?? '';

  // Normalize fractional to [decimals], with rounding if needed
  if (fracStr.length > decimals) {
    final carryAnd = _roundFrac(fracStr, decimals);
    final carry = carryAnd.$1;
    fracStr = carryAnd.$2;
    // Apply carry to integer string
    if (carry) {
      final carried = (BigInt.parse(intStr) + BigInt.one).toString();
      // replace intStr
      return _composeAtto(
        signStr == '-' ? -BigInt.parse('$carried${'0' * decimals}') : BigInt.parse('$carried${'0' * decimals}'),
      );
    }
  } else {
    // Pad with zeros to exact decimals
    fracStr = fracStr.padRight(decimals, '0');
  }

  final full = '$intStr$fracStr';
  if (!RegExp(r'^\d+$').hasMatch(full)) return null;

  final big = BigInt.parse(full);
  return signStr == '-' ? -big : big;
}

/// Format a USD value from an atto amount and USD price (per 1.0 ANM).
/// Example: atto=1e18, price=2.5 -> "$2.50".
String formatFiatUSD(BigInt atto, double usdPerUnit,
    {int decimals = 18, int fractionDigits = 2}) {
  final n = NumberFormat.currency(symbol: r'$', decimalDigits: fractionDigits);
  final asDouble =
      _toDouble(atto, decimals: decimals) * usdPerUnit; // beware: large values lose precision visually only
  return n.format(asDouble);
}

/// Format gas fee: returns string like "0.00005 ANM (50,000 @ 1,000,000)".
String formatFee({
  required int gasLimit,
  required int gasPriceAtto,
  int decimals = 18,
  int precision = 6,
}) {
  final feeAtto =
      BigInt.from(gasLimit) * BigInt.from(gasPriceAtto); // atto-ANM total
  final pretty = formatAmountWithSymbol(feeAtto,
      decimals: decimals, precision: precision, trimZeros: true);
  final limitFmt = NumberFormat.decimalPattern().format(gasLimit);
  final priceFmt = NumberFormat.decimalPattern().format(gasPriceAtto);
  return '$pretty ($limitFmt @ $priceFmt)';
}

/// Format a percentage with fixed decimals, e.g., "2.50%".
String formatPercent(num value, {int fractionDigits = 2}) {
  final n = NumberFormat.decimalPercentPattern(decimalDigits: fractionDigits);
  return n.format(value);
}

/// Simple ISO date-time, local time.
String formatTimestamp(DateTime dt) {
  return DateFormat('yyyy-MM-dd HH:mm:ss').format(dt.toLocal());
}

/// Humanize bytes (1024 base): e.g., 1536 -> "1.5 KiB".
String formatBytes(int bytes, {int fractionDigits = 1}) {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  var size = bytes.toDouble();
  var idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx++;
  }
  return '${size.toStringAsFixed(fractionDigits)} ${units[idx]}';
}

// ---- internal helpers ------------------------------------------------------

/// Round a decimal fraction string to [precision] digits, HALF-UP.
/// Returns (carryToInteger, roundedFractionString).
///
/// Example:
///   _roundFrac('123456', 2) -> (false, '12') because next digit is '3'
///   _roundFrac('129999', 2) -> (true,  '13') because rounding cascades
(bool, String) _roundFrac(String frac, int precision) {
  if (precision <= 0) {
    // Decide integer carry based on first digit
    final carry = frac.isNotEmpty && (frac.codeUnitAt(0) - 48) >= 5;
    return (carry, '');
  }

  if (frac.length <= precision) {
    // No rounding needed; pad handled by caller if necessary
    return (false, frac.padRight(precision, '0'));
  }

  final keep = frac.substring(0, precision).split('');
  final nextDigit = frac.codeUnitAt(precision) - 48; // ascii '0'..'9'
  var carry = nextDigit >= 5;

  // Propagate carry if needed
  for (int i = keep.length - 1; i >= 0 && carry; i--) {
    final d = keep[i].codeUnitAt(0) - 48;
    final nd = d + 1;
    if (nd == 10) {
      keep[i] = '0';
      carry = true;
    } else {
      keep[i] = String.fromCharCode(nd + 48);
      carry = false;
    }
  }
  final rounded = keep.join('');
  return (carry, rounded);
}

/// Group integer part with locale-aware separators (no decimals here).
String _groupInt(String intStr, {required bool useGrouping}) {
  if (!useGrouping) return intStr;
  final nf = NumberFormat.decimalPattern();
  return nf.format(int.parse(intStr));
}

/// 10^n as BigInt.
BigInt _pow10(int n) {
  var r = BigInt.one;
  for (var i = 0; i < n; i++) {
    r *= BigInt.from(10);
  }
  return r;
}

/// Convert atto amount to double for *display only* (can lose precision).
double _toDouble(BigInt atto, {int decimals = 18}) {
  final sign = atto.isNegative ? -1.0 : 1.0;
  final abs = atto.abs();
  final intPart = abs ~/ _pow10(decimals);
  final frac = abs % _pow10(decimals);
  final d = intPart.toDouble() +
      frac.toDouble() / _pow10(decimals).toDouble();
  return sign * d;
}

/// Identity helper (kept for parse round-carry path symmetry).
BigInt _composeAtto(BigInt v) => v;
