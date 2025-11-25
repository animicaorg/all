import 'dart:convert';
import 'package:flutter/foundation.dart';

/// Minimal, dependency-free logger for Animica Wallet.
/// - In **debug/profile**: pretty, human-friendly logs (with emoji & color).
/// - In **release**: structured **JSON lines** for ingestion.
///
/// Usage:
/// ```dart
/// Log.i('App started', cat: 'boot');
/// Log.d('Built tx', cat: 'tx', ctx: {'nonce': 12, 'gas': 50_000});
/// try { ... } catch (e, st) { Log.e('RPC failed', err: e, st: st, cat: 'rpc'); }
/// final t = Log.timer('rpc', 'getBalance');
/// await rpc.getBalance(..);
/// t.ok(); // auto logs elapsed_ms
/// ```
///
/// Tip: In release, ship logs to a collector by tailing stdout/stderr.
enum LogLevel { trace, debug, info, warn, error }

class Log {
  /// Global minimum level. In debug we default to `trace`, in release to `info`.
  static LogLevel minLevel = kReleaseMode ? LogLevel.info : LogLevel.trace;

  /// Toggle ANSI colors for pretty output (ignored in release JSON mode).
  static bool ansi = !kReleaseMode;

  /// Optional extra fields appended to every JSON log line (release only).
  /// e.g., {'app':'animica_wallet','env':'prod'}
  static Map<String, Object?> globalFields = const {};

  // ---------------- public helpers ----------------

  static void t(Object msg, {String? cat, Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      _log(LogLevel.trace, msg, cat: cat, err: err, st: st, ctx: ctx);

  static void d(Object msg, {String? cat, Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      _log(LogLevel.debug, msg, cat: cat, err: err, st: st, ctx: ctx);

  static void i(Object msg, {String? cat, Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      _log(LogLevel.info, msg, cat: cat, err: err, st: st, ctx: ctx);

  static void w(Object msg, {String? cat, Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      _log(LogLevel.warn, msg, cat: cat, err: err, st: st, ctx: ctx);

  static void e(Object msg, {String? cat, Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      _log(LogLevel.error, msg, cat: cat, err: err, st: st, ctx: ctx);

  /// Scoped logger that pre-binds a category.
  static ScopedLog scope(String category) => ScopedLog(category);

  /// Stopwatch helper: call `ok()` or `fail()` to emit the duration.
  static LogTimer timer(String category, String label) => LogTimer._(category, label);

  /// Attach to Flutter error pipeline (optional).
  /// Call once during boot if you want automatic logging of framework errors.
  static void wireFlutterErrors() {
    final prev = FlutterError.onError;
    FlutterError.onError = (FlutterErrorDetails details) {
      e('FlutterError', cat: 'flutter', err: details.exception, st: details.stack);
      prev?.call(details); // keep default console dump in debug
    };
  }

  // ---------------- core ----------------

  static void _log(
    LogLevel level,
    Object msg, {
    String? cat,
    Object? err,
    StackTrace? st,
    Map<String, Object?>? ctx,
  }) {
    if (!_enabled(level)) return;

    final now = DateTime.now();
    if (kReleaseMode) {
      // JSON line (stable keys)
      final Map<String, Object?> json = {
        'ts': now.toIso8601String(),
        'level': _lvl(level),
        'msg': _oneLine('$msg'),
        if (cat != null) 'cat': cat,
        if (ctx != null && ctx.isNotEmpty) 'ctx': _jsonSafe(ctx),
        if (err != null) 'err': _oneLine(err.toString()),
        if (st != null) 'st': _oneLine(st.toString()),
        ...globalFields,
      };
      // Use print so platforms forward to stdout; debugPrint can throttle.
      print(jsonEncode(json));
      return;
    }

    // Pretty (debug/profile)
    final emoji = _emoji(level);
    final time = _hhmmss(now);
    final catStr = cat != null ? _dim('[${cat}]') : '';
    final msgStr = msg.toString();
    final ctxStr = (ctx != null && ctx.isNotEmpty) ? _dim(' ${jsonEncode(_jsonSafe(ctx))}') : '';
    final line = '$time $emoji $catStr $msgStr$ctxStr'.trim();
    debugPrint(line);

    if (err != null) {
      debugPrint(_dim('  ↳ err: ${_oneLine(err.toString())}'));
    }
    if (st != null) {
      // Print a few top frames to keep logs readable.
      final stStr = st.toString().split('\n').take(6).join('\n');
      debugPrint(_dim(stStr));
    }
  }

  static bool _enabled(LogLevel lvl) => lvl.index >= minLevel.index;

  // ---------------- formatting helpers ----------------

  static String _lvl(LogLevel l) {
    switch (l) {
      case LogLevel.trace: return 'trace';
      case LogLevel.debug: return 'debug';
      case LogLevel.info:  return 'info';
      case LogLevel.warn:  return 'warn';
      case LogLevel.error: return 'error';
    }
  }

  static String _emoji(LogLevel l) {
    switch (l) {
      case LogLevel.trace: return ansi ? '\x1B[90m🔍 TRACE\x1B[0m' : '🔍 TRACE';
      case LogLevel.debug: return ansi ? '\x1B[36m🐛 DEBUG\x1B[0m' : '🐛 DEBUG';
      case LogLevel.info:  return ansi ? '\x1B[32mℹ️  INFO\x1B[0m'  : 'ℹ️  INFO';
      case LogLevel.warn:  return ansi ? '\x1B[33m⚠️  WARN\x1B[0m'  : '⚠️  WARN';
      case LogLevel.error: return ansi ? '\x1B[31m❌ ERROR\x1B[0m' : '❌ ERROR';
    }
  }

  static String _dim(String s) => ansi ? '\x1B[2m$s\x1B[0m' : s;

  static String _hhmmss(DateTime dt) {
    final h = dt.hour.toString().padLeft(2, '0');
    final m = dt.minute.toString().padLeft(2, '0');
    final s = dt.second.toString().padLeft(2, '0');
    final ms = dt.millisecond.toString().padLeft(3, '0');
    return '$h:$m:$s.$ms';
    }

  static Map<String, Object?> _jsonSafe(Map<String, Object?> m) {
    // Convert non-encodable values to strings.
    return m.map((k, v) {
      if (v == null) return MapEntry(k, null);
      if (v is num || v is String || v is bool || v is List || v is Map) {
        return MapEntry(k, v);
      }
      return MapEntry(k, v.toString());
    });
  }

  static String _oneLine(String s) {
    // Collapse whitespace and line breaks to keep JSON lines compact.
    final t = s.replaceAll(RegExp(r'\s+'), ' ');
    return t.length > 10_000 ? '${t.substring(0, 9990)}…<truncated>' : t;
  }
}

/// Scoped logger that pre-binds a category.
/// Example:
///   final log = Log.scope('rpc');
///   log.i('starting');
///   log.e('boom', err: e, st: st);
class ScopedLog {
  final String category;
  const ScopedLog(this.category);

  void t(Object msg, {Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      Log.t(msg, cat: category, err: err, st: st, ctx: ctx);

  void d(Object msg, {Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      Log.d(msg, cat: category, err: err, st: st, ctx: ctx);

  void i(Object msg, {Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      Log.i(msg, cat: category, err: err, st: st, ctx: ctx);

  void w(Object msg, {Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      Log.w(msg, cat: category, err: err, st: st, ctx: ctx);

  void e(Object msg, {Object? err, StackTrace? st, Map<String, Object?>? ctx}) =>
      Log.e(msg, cat: category, err: err, st: st, ctx: ctx);
}

/// Stopwatch wrapper that logs elapsed time on completion.
/// Call [ok] for success or [fail] for failure (adds `ok:false` flag).
class LogTimer {
  final String category;
  final String label;
  final Stopwatch _sw = Stopwatch()..start();

  LogTimer._(this.category, this.label);

  void ok({Map<String, Object?> ctx = const {}}) {
    _sw.stop();
    Log.i('done $label', cat: category, ctx: {
      ...ctx,
      'elapsed_ms': _sw.elapsedMilliseconds,
      'ok': true,
    });
  }

  void fail({Object? err, StackTrace? st, Map<String, Object?> ctx = const {}}) {
    _sw.stop();
    Log.e('fail $label', cat: category, err: err, st: st, ctx: {
      ...ctx,
      'elapsed_ms': _sw.elapsedMilliseconds,
      'ok': false,
    });
  }
}
