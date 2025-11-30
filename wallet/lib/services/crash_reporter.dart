// Minimal crash reporter facade used by the app. This keeps boot logic simple
// and avoids a hard dependency on Sentry/Firebase at this time. If a DSN is
// provided via `Env.sentryDsn`, this file will log that reporting is enabled
// but will not attempt to import an external package unless you add it to
// pubspec.yaml and wire it here.

import 'package:flutter/foundation.dart';
import 'services/env.dart' show Env;

class CrashReporter {
  static Env? _env;

  /// Initialize the reporter with runtime environment. Call early in main().
  static Future<void> init(Env env) async {
    _env = env;
    if (env.sentryDsn != null && env.sentryDsn!.isNotEmpty) {
      // We deliberately avoid a hard dependency here. If you want real
      // Sentry integration, add `sentry_flutter` to pubspec.yaml and wire
      // it here (SentryFlutter.init(...)). For now, log the configuration.
      if (kDebugMode) {
        // ignore: avoid_print
        print('CrashReporter: SENTRY configured (dsn present) — external reporting disabled in this build.');
      }
    }
  }

  /// Report an error+stack. This will at minimum print the error to console.
  /// If a reporting DSN is available and an integration is added, it can
  /// forward the error to Sentry/Firebase.
  static Future<void> reportError(Object error, StackTrace stack) async {
    // Always print locally for diagnostics
    if (kDebugMode) {
      // ignore: avoid_print
      print('CrashReporter caught: $error\n$stack');
    }

    // Placeholder: if a DSN is configured, you could forward to an installed
    // crash-reporting SDK here. We leave that as an opt-in wiring step.
  }
}
