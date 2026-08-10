// App-wide constants. Pull from `--dart-define` at build time when
// shipping different builds (devnet vs mainnet, alternate gateway).
library;

class AnimicaConfig {
  static const String chainName =
      String.fromEnvironment('ANIMICA_CHAIN', defaultValue: 'mainnet');

  static const int chainId =
      int.fromEnvironment('ANIMICA_CHAIN_ID', defaultValue: 1);

  /// Public JSON-RPC endpoints, tried in order; the client remembers the
  /// last-good one and short-circuits endpoints that recently failed at the
  /// transport level (see RpcClient), so one dead host never stalls a send.
  /// `rpc.animica.org` is the canonical public node (read-through cache with
  /// node fallback); `animica.dev/rpc` proxies the same node directly.
  /// `mobile.animica.org` is kept last: its DNS pointed at a decommissioned
  /// box in 2026-07 and made it a 12-second tarpit as the old PRIMARY —
  /// every wallet session stalled and flaky networks saw sends fail outright.
  static const List<String> rpcEndpoints = [
    String.fromEnvironment(
      'ANIMICA_RPC_URL_PRIMARY',
      defaultValue: 'https://rpc.animica.org/rpc',
    ),
    String.fromEnvironment(
      'ANIMICA_RPC_URL_FALLBACK',
      defaultValue: 'https://animica.dev/rpc',
    ),
    String.fromEnvironment(
      'ANIMICA_RPC_URL_TERTIARY',
      defaultValue: 'https://mobile.animica.org/rpc',
    ),
  ];

  /// Legacy single-endpoint name kept for any callers that still expect
  /// a single string. Points to the primary.
  static String get rpcUrl => rpcEndpoints.first;

  /// Buy gateway URL — the legacy NOWPayments desk. Kept only as a
  /// historical reference; it currently serves 503, so the Buy screen no
  /// longer links to it (see lib/screens/buy.dart).
  static const String buyGatewayUrl = String.fromEnvironment(
    'ANIMICA_BUY_URL',
    defaultValue: 'https://buy.animica.org',
  );

  /// PayPal on-ramp desk base. nginx on animica.dev strips the `/api`
  /// prefix and proxies to the buy-sol Fastify service (loopback :4510),
  /// so the wallet talks to `/api/onramp/...`. These routes are DORMANT
  /// until the operator arms the rail (ONRAMP_ENABLED + ONRAMP_PAYPAL_ENABLED);
  /// the Buy screen degrades to an "almost ready" state on 404/503.
  static const String onrampApiUrl = String.fromEnvironment(
    'ANIMICA_ONRAMP_URL',
    defaultValue: 'https://animica.dev/api/onramp',
  );

  /// Block height at which on-chain contract execution (FORK_VM_EXEC, network
  /// upgrade 9.6.0) activates — the point swaps/liquidity/token-init start
  /// settling. Below it those calls revert (deploys + transfers already work).
  /// 0 on devnet/testnet (active from genesis). Keep in sync with the node's
  /// `core/network_params.FORK_VM_EXEC` height.
  static const int vmExecActivationHeight =
      int.fromEnvironment('ANIMICA_VM_EXEC_HEIGHT', defaultValue: 75000);

  /// A code-committed, mainnet-deployed contract used only as a liveness probe
  /// for on-chain contract execution (the DEX capability check calls its
  /// `owner()` view). This is the NFT marketplace from
  /// contracts/deployments/nft_marketplace.mainnet.json.
  static const String probeContract = String.fromEnvironment(
    'ANIMICA_PROBE_CONTRACT',
    defaultValue: 'anim1qqq26fkt5pf0z8vehl6emafreyt7x48ly7lxaqx72aq73ehhme9v7js3zp3lg',
  );

  /// The DEX factory/router contract addresses, once deployed and governed.
  /// Empty until the operator publishes them (the DEX tab shows a "not yet
  /// deployed" state rather than guessing an address).
  static const String dexFactoryAddress =
      String.fromEnvironment('ANIMICA_DEX_FACTORY', defaultValue: '');
  static const String dexRouterAddress =
      String.fromEnvironment('ANIMICA_DEX_ROUTER', defaultValue: '');

  /// Off-chain token index (metadata, images, links, promoted set, price
  /// history for charts) — the explorer's token tracker, live at
  /// explorer.animica.org/api. The client appends `/tokens/...` to this base.
  /// Set to empty via --dart-define to run the DEX tab in on-chain-only mode.
  static const String tokenIndexUrl = String.fromEnvironment(
    'ANIMICA_TOKEN_INDEX_URL',
    defaultValue: 'https://explorer.animica.org/api',
  );

  /// NonKYC public market-data endpoint for the ANM/USDT pair — prices
  /// wallet balances in fiat on the Home screen. Plain GET, no auth, no
  /// API key. NonKYC is the exchange where ANM actually trades, so its
  /// last price is the reference quote.
  static const String priceApiUrl = String.fromEnvironment(
    'ANIMICA_PRICE_URL',
    defaultValue: 'https://api.nonkyc.io/api/v2/market/getbysymbol/ANM_USDT',
  );

  /// Marketplace API for NFT lookups (per-wallet collection view).
  static const String marketplaceUrl = String.fromEnvironment(
    'ANIMICA_MARKETPLACE_URL',
    defaultValue: 'https://animica.xyz',
  );

  /// App Store / marketplace REST base (the `mkt/v1` root on animica.dev).
  /// Both the store catalog/purchase routes (`/store/...`) and the wallet
  /// challenge-login routes (`/auth/...`) hang off this. Content, icon and
  /// APK-download URLs the API returns are ORIGIN-relative (`/api/mkt/v1/...`)
  /// and are resolved against this base's origin by MarketplaceApi.
  static const String storeApiUrl = String.fromEnvironment(
    'ANIMICA_STORE_API_URL',
    defaultValue: 'https://animica.dev/api/mkt/v1',
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
  static final BigInt nanosPerAnm = BigInt.from(1000000000);

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
