import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Load a local .env file when present. Safe to call in dev/test only.
///
/// In release builds, the loader no-ops to avoid shipping accidental secrets.
Future<void> loadDotEnvIfPresent() async {
  if (kReleaseMode) return;

  try {
    await dotenv.load(fileName: '.env', isOptional: true);
  } catch (_) {
    // Swallow errors: env loading is best-effort for local development.
  }
}
