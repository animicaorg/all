/*
 * Animica Wallet — Environment / Flavor config (dev • test • prod)
 *
 * Usage:
 *   flutter run \
 *     --dart-define=FLAVOR=dev \
 *     --dart-define=RPC_URL=https://localhost:8545 \
 *     --dart-define=WS_URL=ws://localhost:8546 \
 *     --dart-define=CHAIN_ID=1337 \
 *     --dart-define=FEATURE_FLAGS=devtools,experimental_pq
 *
 * Defaults (when not provided via --dart-define):
 *   • FLAVOR      → inferred from kReleaseMode (prod if release, else dev)
 *   • RPC_URL     → per-flavor fallback (see _defaultsByFlavor)
 *   • WS_URL      → empty (disabled)
 *   • CHAIN_ID    → 2 (Animica testnet placeholder)
 *   • FEATURE_FLAGS → none
 */

import 'dart:io' show Platform;
import 'package:flutter/foundation.dart' show kReleaseMode;

enum AppFlavor { dev, test, prod }

class AppEnv {
  final AppFlavor flavor;
  final Uri rpcHttp;
  final Uri? rpcWs;
  final int chainId;
  final Map<String, bool> featureFlags; // e.g. {"devtools": true, "experimental_pq": true}
  final String userAgent;

  const AppEnv({
    required this.flavor,
    required this.rpcHttp,
    required this.rpcWs,
    required this.chainId,
    required this.featureFlags,
    required this.userAgent,
  });

  bool get isDev => flavor == AppFlavor.dev;
  bool get isTest => flavor == AppFlavor.test;
  bool get isProd => flavor == AppFlavor.prod;

  bool flag(String name) => featureFlags[name] == true;

  @override
  String toString() =>
      'Env(flavor=$flavor rpcHttp=$rpcHttp rpcWs=$rpcWs chainId=$chainId flags=${featureFlags.keys.toList()})';

  /// Construct from --dart-define values with sensible fallbacks.
  factory AppEnv.fromDartDefine() {
    final flavor = _readFlavor();
    final defaults = _defaultsByFlavor(flavor);

    final rpcHttpStr =
        const String.fromEnvironment('RPC_URL', defaultValue: '');
    final rpcWsStr =
        const String.fromEnvironment('WS_URL', defaultValue: '');

    final chainId =
        const int.fromEnvironment('CHAIN_ID', defaultValue: 2);

    final flagsStr =
        const String.fromEnvironment('FEATURE_FLAGS', defaultValue: '');

    final rpcHttp = Uri.parse(
      rpcHttpStr.isNotEmpty ? rpcHttpStr : defaults.http,
    );

    final Uri? rpcWs =
        rpcWsStr.isNotEmpty ? Uri.parse(rpcWsStr) : (defaults.ws?.isNotEmpty == true ? Uri.parse(defaults.ws!) : null);

    final featureFlags = _parseFlags(flagsStr);

    final ua = _buildUserAgent(flavor);

    return AppEnv(
      flavor: flavor,
      rpcHttp: rpcHttp,
      rpcWs: rpcWs,
      chainId: chainId,
      featureFlags: featureFlags,
      userAgent: ua,
    );
  }
}

/// Global singleton, initialize once at app boot (see main.dart).
late final AppEnv env;

/// Initialize [env] with optional override (useful for tests).
void initEnv([AppEnv? override]) {
  env = override ?? AppEnv.fromDartDefine();
}

// ---------------- Internals ----------------

class _FlavorDefaults {
  final String http;
  final String? ws;
  const _FlavorDefaults(this.http, this.ws);
}

_FlavorDefaults _defaultsByFlavor(AppFlavor f) {
  switch (f) {
    case AppFlavor.dev:
      // Local devnet / emulator defaults
      return const _FlavorDefaults('http://127.0.0.1:8545', 'ws://127.0.0.1:8546');
    case AppFlavor.test:
      // Public Animica testnet (placeholder; adjust to your infra)
      return const _FlavorDefaults('https://rpc.testnet.animica.dev', 'wss://ws.testnet.animica.dev');
    case AppFlavor.prod:
      // Mainnet (placeholder; adjust when mainnet launches)
      return const _FlavorDefaults('https://rpc.animica.org', 'wss://ws.animica.org');
  }
}

AppFlavor _readFlavor() {
  final raw = const String.fromEnvironment('FLAVOR', defaultValue: '');
  switch (raw.toLowerCase()) {
    case 'dev':
      return AppFlavor.dev;
    case 'test':
    case 'staging':
      return AppFlavor.test;
    case 'prod':
    case 'production':
      return AppFlavor.prod;
  }
  // If not specified, infer from build mode.
  return kReleaseMode ? AppFlavor.prod : AppFlavor.dev;
}

Map<String, bool> _parseFlags(String csv) {
  // Accept: "a,b,!c" → {"a":true,"b":true,"c":false}
  final out = <String, bool>{};
  if (csv.trim().isEmpty) return out;
  for (final raw in csv.split(',')) {
    final s = raw.trim();
    if (s.isEmpty) continue;
    if (s.startsWith('!') && s.length > 1) {
      out[s.substring(1)] = false;
    } else {
      out[s] = true;
    }
  }
  return out;
}

String _buildUserAgent(AppFlavor flavor) {
  final os = _platformTag();
  final f = switch (flavor) {
    AppFlavor.dev => 'dev',
    AppFlavor.test => 'test',
    AppFlavor.prod => 'prod',
  };
  // You can append app version here if you inject it via dart-define.
  final ver = const String.fromEnvironment('APP_VERSION', defaultValue: '0.1.0');
  return 'animica-wallet/$ver ($os; $f)';
}

String _platformTag() {
  try {
    if (Platform.isIOS) return 'iOS';
    if (Platform.isAndroid) return 'Android';
    if (Platform.isMacOS) return 'macOS';
    if (Platform.isWindows) return 'Windows';
    if (Platform.isLinux) return 'Linux';
  } catch (_) {
    // Web or unsupported platform
  }
  return 'Web';
}
