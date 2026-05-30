// App-wide constants. Pull from `--dart-define` at build time when
// shipping different builds (devnet vs mainnet, alternate gateway).
library;

class AnimicaConfig {
  static const String chainName =
      String.fromEnvironment('ANIMICA_CHAIN', defaultValue: 'mainnet');

  static const int chainId =
      int.fromEnvironment('ANIMICA_CHAIN_ID', defaultValue: 1);

  /// Primary + secondary public JSON-RPC endpoints. The client tries them
  /// in order and remembers the last-good one for the session, so a single
  /// flap doesn't punish every subsequent call. `mobile.animica.org/rpc`
  /// is a smaller node optimized for mobile wallet traffic; `rpc.animica.org/rpc`
  /// is the general public node and acts as the failover.
  static const List<String> rpcEndpoints = [
    String.fromEnvironment(
      'ANIMICA_RPC_URL_PRIMARY',
      defaultValue: 'https://mobile.animica.org/rpc',
    ),
    String.fromEnvironment(
      'ANIMICA_RPC_URL_FALLBACK',
      defaultValue: 'https://rpc.animica.org/rpc',
    ),
  ];

  /// Legacy single-endpoint name kept for any callers that still expect
  /// a single string. Points to the primary.
  static String get rpcUrl => rpcEndpoints.first;

  /// Buy gateway URL — opened by the Buy screen.
  static const String buyGatewayUrl = String.fromEnvironment(
    'ANIMICA_BUY_URL',
    defaultValue: 'https://buy.animica.org',
  );

  /// Marketplace API for NFT lookups (per-wallet collection view).
  static const String marketplaceUrl = String.fromEnvironment(
    'ANIMICA_MARKETPLACE_URL',
    defaultValue: 'https://animica.xyz',
  );

  /// Block explorer link template — `{txHash}` and `{address}` are replaced.
  static const String explorerTxUrl =
      'https://explorer.animica.org/tx/{txHash}';
  static const String explorerAddressUrl =
      'https://explorer.animica.org/address/{address}';

  /// Whitelist of origins the dapp browser may inject `window.animica` into.
  /// Keep tight — any page in this list can prompt the user to sign txs.
  /// Wildcards: a trailing `*` matches any suffix on the host.
  static const List<String> walletProviderHosts = [
    'animica.xyz',
    'www.animica.xyz',
    'buy.animica.org',
    'animica.org',
    'www.animica.org',
    'explorer.animica.org',
    'pool.animica.org',
  ];

  /// 1 ANM in nano-units.
  static const BigInt nanosPerAnm = BigInt.from(1000000000);

  /// SPHINCS-SHAKE-128s parameters (Animica pure-python variant —
  /// pubkey is the 64-byte form `_h("pk", sk, out_len=64)`, sig is
  /// 7856 bytes).
  static const int sphincsPubkeyLen = 64;
  static const int sphincsSecretLen = 64;
  static const int sphincsSigLen = 7856;
  static const int algIdSphincs = 0x1002;

  /// Dilithium3 parameters — DEPRECATED commitment-stub scheme.
  static const int dilithiumPubkeyLen = 1952;
  static const int dilithiumSecretLen = 4000;
  static const int dilithiumSigLen = 3293;
  static const int algIdDilithium3 = 0x1001;

  /// ML-DSA-65 (real FIPS 204) — chain v2 canonical scheme.
  /// Signing in Dart requires a port of FIPS 204 ML-DSA-65 (vendored
  /// at python/animica/_vendor/dilithium_py_v2/ on the node side);
  /// until that lands the wallet can display/import/receive ml_dsa_65
  /// addresses but must sign outbound txs via the `animica` CLI.
  static const int mlDsa65PubkeyLen = 1952;
  static const int mlDsa65SecretLen = 4032;
  static const int mlDsa65SigLen = 3309;
  static const int algIdMlDsa65 = 0x1003;
}
