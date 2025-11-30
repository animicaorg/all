// ignore_for_file: prefer_const_constructors

import 'dart:async';
import 'package:flutter/foundation.dart' show FlutterError, kDebugMode, kReleaseMode;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart'; // Will host AnimicaApp
import 'services/env.dart' show Env; // Env.bootstrap(String flavor)
import 'services/crash_reporter.dart' show CrashReporter;
import 'tool/env_loader.dart' as env_loader; // loadDotEnvIfPresent()

/// Compile-time flavor: pass with
///   flutter run  --dart-define=FLAVOR=dev
///   flutter run  --dart-define=FLAVOR=test
///   flutter run  --dart-define=FLAVOR=prod
const String kFlavor = String.fromEnvironment('FLAVOR', defaultValue: 'dev');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Edge-to-edge UI and portrait default; tweak per your needs.
  await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  await SystemChrome.setPreferredOrientations(<DeviceOrientation>[
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
  ]);

  // Load local .env only for non-release builds (no secrets in prod).
  try {
    if (!kReleaseMode) {
      await env_loader.loadDotEnvIfPresent();
    }
  } catch (_) {
    // Non-fatal: continue without .env if loader not present yet.
  }

  // Global error wiring
  FlutterError.onError = (FlutterErrorDetails details) {
    // Always log to console.
    FlutterError.dumpErrorToConsole(details);
    // Forward to the crash reporter (no-op if not configured)
    try {
      CrashReporter.reportError(details.exception, details.stack ?? StackTrace.current);
    } catch (_) {}
  };

  // Bootstrap environment (URLs, chainId, feature flags) based on flavor.
  // Env is provided by wallet/lib/services/env.dart in this repo plan.
  final Env env = await _bootstrapEnv();

  // Initialize crash reporting (optional)
  await CrashReporter.init(env);

  // Riverpod scope + app
  runZonedGuarded(
    () => runApp(
      ProviderScope(
        overrides: [
          // Optionally provide env as a Riverpod override later, e.g. envProvider.overrideWithValue(env)
        ],
        child: AnimicaApp(env: env, flavor: kFlavor),
      ),
    ),
    (Object error, StackTrace stack) {
      // Last-chance error sink (isolate/zone).
      // Forward to crash reporter and print locally in debug.
      try {
        CrashReporter.reportError(error, stack);
      } catch (_) {}
      if (kDebugMode) {
        // ignore: avoid_print
        print('Uncaught zone error: $error\n$stack');
      }
    },
  );
}

Future<Env> _bootstrapEnv() async {
  try {
    return await Env.bootstrap(kFlavor);
  } catch (e, st) {
    // Provide a minimal fallback so the app can still render a basic shell.
    if (kDebugMode) {
      // ignore: avoid_print
      print('Env.bootstrap failed ($e). Using fallback dev env.\n$st');
    }
    // This constructor will exist in services/env.dart; keep in sync there.
    return Env.fallback(flavor: kFlavor);
  }
}
