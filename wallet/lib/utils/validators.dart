import 'dart:convert' show utf8;

import '../constants.dart';
import 'result.dart';
import 'format.dart' as fmt;

/// Address validation issues.
enum AddressIssue {
  empty,
  mixedCase,
  wrongHrp,
  invalidFormat,
  checksumFailed, // only when strict validator is provided and fails
}

/// Amount validation issues.
enum AmountIssue {
  empty,
  notNumber,
  negative,
  belowMin,
  aboveMax,
}

/// Gas-related validation issues.
enum GasIssue {
  notPositive,
  aboveMax,
}

/// Memo validation issues.
enum MemoIssue {
  tooLong,
}

class Validators {
  /// Validate an Animica address.
  ///
  /// - If [strict] is true and [bech32mValidate] is provided, we call it to
  ///   check checksum and charset rules; on failure -> `checksumFailed`.
  /// - Otherwise we perform a strong heuristic check:
  ///   * non-empty
  ///   * no mixed-case (bech32 requires all lower or all upper)
  ///   * HRP (before '1') matches `AddressFormat.hrp` (default "am")
  ///   * matches quick bech32 pattern `AddressFormat.quickBech32`
  ///
  /// Returns the normalized (lowercased) address on success.
  static Result<AddressIssue, String> address(
    String input, {
    bool strict = false,
    bool Function(String addr)? bech32mValidate,
  }) {
    final raw = input.trim();
    if (raw.isEmpty) return Result.err(AddressIssue.empty);

    // Bech32 forbids mixed case.
    final hasLower = raw.contains(RegExp(r'[a-z]'));
    final hasUpper = raw.contains(RegExp(r'[A-Z]'));
    if (hasLower && hasUpper) {
      return Result.err(AddressIssue.mixedCase);
    }

    final addr = raw.toLowerCase();

    // HRP check (segment before '1')
    final sep = addr.indexOf('1');
    final hrp = sep > 0 ? addr.substring(0, sep) : '';
    if (hrp != AddressFormat.hrp) {
      return Result.err(AddressIssue.wrongHrp);
    }

    // Quick format check
    if (!fmt.looksLikeAnimicaAddress(addr)) {
      return Result.err(AddressIssue.invalidFormat);
    }

    // Optional strict validation via provided bech32m checker (e.g., crypto/bech32m.dart)
    if (strict && bech32mValidate != null) {
      final ok = bech32mValidate(addr);
      if (!ok) return Result.err(AddressIssue.checksumFailed);
    }

    return Result.ok(addr);
  }

  /// Validate a user-entered amount string and return atto-ANM (BigInt).
  ///
  /// - Accepts grouping and symbols (`, $` etc. are stripped).
  /// - Rounds HALF-UP if too many fractional digits.
  /// - Enforces [min] and [max] bounds (defaults to UI rails from [TxLimits]).
  static Result<AmountIssue, BigInt> amount(
    String input, {
    int decimals = 18,
    BigInt? min,
    BigInt? max,
  }) {
    final s = input.trim();
    if (s.isEmpty) return Result.err(AmountIssue.empty);

    final parsed = fmt.parseAmountToAtto(s, decimals: decimals);
    if (parsed == null) return Result.err(AmountIssue.notNumber);
    if (parsed.isNegative) return Result.err(AmountIssue.negative);

    final lo = min ?? TxLimits.minAmount;
    final hi = max ?? TxLimits.maxAmount;

    if (parsed < lo) return Result.err(AmountIssue.belowMin);
    if (parsed > hi) return Result.err(AmountIssue.aboveMax);

    return Result.ok(parsed);
  }

  /// Validate gas limit (must be positive and below UI cap).
  static Result<GasIssue, int> gasLimit(int value) {
    if (value <= 0) return Result.err(GasIssue.notPositive);
    if (value > Gas.maxLimitUI) return Result.err(GasIssue.aboveMax);
    return Result.ok(value);
  }

  /// Validate gas price (atto per unit; positive and below UI cap).
  static Result<GasIssue, int> gasPrice(int value) {
    if (value <= 0) return Result.err(GasIssue.notPositive);
    if (value > Gas.maxPriceUI) return Result.err(GasIssue.aboveMax);
    return Result.ok(value);
  }

  /// Validate memo length in bytes (UTF-8) against [TxLimits.memoMaxUtf8Bytes].
  /// Empty/null is considered OK and returns normalized empty string.
  static Result<MemoIssue, String> memo(String? memo, {int? maxBytes}) {
    final s = (memo ?? '').trimRight();
    if (s.isEmpty) return Result.ok('');

    final limit = maxBytes ?? TxLimits.memoMaxUtf8Bytes;
    final len = utf8.encode(s).length;
    if (len > limit) return Result.err(MemoIssue.tooLong);
    return Result.ok(s);
  }
}
