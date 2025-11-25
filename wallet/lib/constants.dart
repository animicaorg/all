/*
 * Animica Wallet — Constants
 * Chain IDs, RPC defaults, fee/tx limits, and misc knobs used across the app.
 *
 * NOTE: Runtime selection (dev/test/prod) is handled in services/env.dart.
 * These are sensible defaults and fallbacks if env is missing/incomplete.
 */

import 'package:flutter/foundation.dart';

/// Numeric chain IDs (align with chains/: 1, 2, 1337)
class ChainIds {
  static const int mainnet  = 1;     // reserved; placeholders until launch
  static const int testnet  = 2;     // public dev/test network
  static const int localnet = 1337;  // local dev profile
  static const List<int> all = [mainnet, testnet, localnet];
}

/// Human-readable monikers for display and logs.
class ChainNames {
  static const String mainnet  = 'Animica Mainnet';
  static const String testnet  = 'Animica Testnet';
  static const String localnet = 'Animica Localnet';
}

/// Address format
class AddressFormat {
  static const String hrp = 'am';          // bech32m human-readable prefix
  static const List<String> pubkeyTypes = [
    'ed25519', 'secp256k1', 'dilithium3'
  ];
  // Quick-and-loose validator for UI (real validation lives in bech32m.dart)
  static final RegExp quickBech32 =
      RegExp(r'^am1[02-9ac-hj-np-z]{8,}$', caseSensitive: false);
}

/// Default RPC endpoints (HTTP & WS) per chain.
/// Replace these with your actual infra; these are placeholders/sane dev defaults.
class RpcDefaults {
  static const Map<int, List<String>> http = {
    ChainIds.mainnet: [
      // Placeholders — update when mainnet endpoints are live
      'https://rpc.animica.org',
    ],
    ChainIds.testnet: [
      'https://rpc.testnet.animica.org',
    ],
    ChainIds.localnet: [
      // Local node (common ports)
      'http://127.0.0.1:8545',
      'http://localhost:8545',
    ],
  };

  static const Map<int, List<String>> ws = {
    ChainIds.mainnet: [
      'wss://ws.animica.org',
    ],
    ChainIds.testnet: [
      'wss://ws.testnet.animica.org',
    ],
    ChainIds.localnet: [
      'ws://127.0.0.1:8546',
      'ws://localhost:8546',
    ],
  };

  /// Convenience: pick first available endpoint for a chain.
  static String httpPrimary(int chainId) =>
      (http[chainId] ?? const []).isNotEmpty ? http[chainId]!.first : '';

  static String wsPrimary(int chainId) =>
      (ws[chainId] ?? const []).isNotEmpty ? ws[chainId]!.first : '';
}

/// Default Explorer base URLs per chain (for deep-links TX/Address).
class ExplorerDefaults {
  static const Map<int, String> base = {
    ChainIds.mainnet:  'https://explorer.animica.org',
    ChainIds.testnet:  'https://explorer.testnet.animica.org',
    ChainIds.localnet: 'http://localhost:3000', // if running explorer locally
  };

  static String txUrl(int chainId, String txHash) {
    final b = base[chainId] ?? '';
    return b.isEmpty ? '' : '$b/tx/$txHash';
  }

  static String addressUrl(int chainId, String address) {
    final b = base[chainId] ?? '';
    return b.isEmpty ? '' : '$b/address/$address';
  }
}

/// Gas schedule & fee knobs (keep in sync with chain/vm where possible)
class Gas {
  /// Simple transfer gas limit default.
  static const int defaultTransferLimit = 50_000;

  /// Safe UI default gas limit for generic contract calls (adjustable in UI).
  static const int defaultContractLimit = 250_000;

  /// Minimum gas price (in atto-ANM per unit) the wallet will attempt by default.
  /// Matches sample payloads used in dev flows.
  static const int defaultPrice = 1_000_000; // 1e6

  /// Bump policy for resubmits (linear)
  static const int bumpDelta = 250_000;

  /// Hard caps to prevent user errors
  static const int maxLimitUI = 5_000_000; // 5M units
  static const int maxPriceUI = 50_000_000; // 5e7
}

/// Transaction & memo limits
class TxLimits {
  /// Max memo length the UI will allow (actual chain limit may differ).
  static const int memoMaxUtf8Bytes = 140;

  /// Minimum/maximum sendable amount in the UI layer (safety rails only).
  static const BigInt minAmount = BigInt.from(1);                // 1 wei (atto-ANM)
  static final BigInt maxAmount = BigInt.parse('999999999000000000000000'); // ~1e24
}

/// Networking knobs for RPC/WS clients
class Net {
  static const Duration httpTimeout = Duration(seconds: 20);
  static const Duration receiptPollInterval = Duration(seconds: 2);
  static const Duration receiptMaxWait = Duration(minutes: 2);

  /// Retry policy for transient HTTP failures
  static const int httpMaxRetries = 3;
  static const List<Duration> httpRetryBackoff = <Duration>[
    Duration(milliseconds: 200),
    Duration(milliseconds: 500),
    Duration(seconds: 1),
  ];

  /// WS auto-reconnect
  static const Duration wsReconnectMin = Duration(seconds: 1);
  static const Duration wsReconnectMax = Duration(seconds: 10);
}

/// App feature flags — can be overridden by Env/flavor at runtime.
class FeatureFlags {
  static const bool enableContracts     = true;
  static const bool enableDevTools      = !kReleaseMode;
  static const bool enableQrScanner     = true;
  static const bool showFiatEstimations = true; // if price service available
}

/// Convenience helpers
class Constants {
  static String chainName(int chainId) {
    switch (chainId) {
      case ChainIds.mainnet:  return ChainNames.mainnet;
      case ChainIds.testnet:  return ChainNames.testnet;
      case ChainIds.localnet: return ChainNames.localnet;
      default:                return 'Chain $chainId';
    }
  }

  static bool isSupportedChain(int chainId) =>
      ChainIds.all.contains(chainId);
}
