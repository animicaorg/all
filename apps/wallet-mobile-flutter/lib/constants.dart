// App-wide constants. Pull from `--dart-define` at build time when
// shipping different builds (devnet vs mainnet, alternate gateway).
library;

class AnimicaConfig {
  static const String chainName =
      String.fromEnvironment('ANIMICA_CHAIN', defaultValue: 'mainnet');

  static const int chainId =
      int.fromEnvironment('ANIMICA_CHAIN_ID', defaultValue: 1);

  /// Public JSON-RPC endpoint.
  static const String rpcUrl = String.fromEnvironment(
    'ANIMICA_RPC_URL',
    defaultValue: 'https://rpc.animica.org/rpc',
  );

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

  /// Dilithium3 parameters — only relevant once the Dart port lands.
  static const int dilithiumPubkeyLen = 1952;
  static const int dilithiumSecretLen = 4000;
  static const int dilithiumSigLen = 3293;
  static const int algIdDilithium3 = 0x1001;
}
