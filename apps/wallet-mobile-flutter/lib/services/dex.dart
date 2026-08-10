// Animica token launcher + DEX client.
//
// This is the wallet's half of the on-chain token/AMM stack. The contracts it
// drives are the repo's audited standards, bundled verbatim as app assets
// (assets/contracts/animica_{token,dex_pair,dex_router,dex_factory}) and
// deployed with their raw `contract.py` source as the deploy `code` bytes —
// exactly what execution.runtime.contracts resolves at call time.
//
// IMPORTANT — network execution gate. Since the 6.0.0 hardening, contract
// execution (calls, deploy-time init, and even read-only `state.call`) is
// fail-closed on a node unless it wires the metered VM engine
// (vm_py/runtime/loader.run_call refuses the raw-exec path). Mainnet does not
// enable it yet, so swaps, liquidity and launch-init REVERT and quotes read as
// unavailable there. Two things still work everywhere: contract *deploys*
// (they only store code), and native ANM *transfers* — which is why the
// "promote your token" feature (a transfer to the foundation treasury) is live
// today while the AMM waits on the node. [DexCapability] surfaces this so the
// UI can be honest instead of silently reverting.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;

import '../constants.dart';
import 'abi_codec.dart';
import 'address.dart';
import 'rpc.dart';
import 'signer.dart';

/// Whether on-chain contract execution is live on the connected node.
enum DexCapability { unknown, enabled, disabled }

/// A bundled standard contract: its raw source (deploy `code`), its manifest
/// bytes (deploy `manifest`), and its parsed function ABI (for calldata).
class ContractTemplate {
  final String name;
  final Uint8List codeBytes;
  final Uint8List manifestBytes;
  final List<AbiFn> abi;
  const ContractTemplate({
    required this.name,
    required this.codeBytes,
    required this.manifestBytes,
    required this.abi,
  });
}

/// Parameters for launching a new ANM-20 token.
class TokenLaunchParams {
  final String name;
  final String symbol;
  final int decimals;

  /// Human-readable initial supply (e.g. 1_000_000); scaled by 10^decimals.
  final BigInt initialSupply;

  /// 0 = uncapped.
  final BigInt maxSupply;
  final bool mintable;

  /// Off-chain metadata URI (ipfs://… or https://…) holding the token's image,
  /// description and links. Empty allowed.
  final String metadataUri;

  // Not const: BigInt.zero is not a compile-time constant, so `maxSupply`
  // defaults inside the body rather than in the initializer list.
  TokenLaunchParams({
    required this.name,
    required this.symbol,
    required this.decimals,
    required this.initialSupply,
    BigInt? maxSupply,
    this.mintable = false,
    this.metadataUri = '',
  }) : maxSupply = maxSupply ?? BigInt.zero;

  BigInt get initialSupplyBase => initialSupply * BigInt.from(10).pow(decimals);
  BigInt get maxSupplyBase =>
      maxSupply == BigInt.zero ? BigInt.zero : maxSupply * BigInt.from(10).pow(decimals);
}

class DexService {
  final RpcClient rpc;
  DexService(this.rpc);

  final Map<String, ContractTemplate> _templates = {};

  static const _assetRoot = 'assets/contracts';
  static const _templateNames = {
    'token': 'animica_token',
    'pair': 'animica_dex_pair',
    'router': 'animica_dex_router',
    'factory': 'animica_dex_factory',
  };

  /// Load (and cache) a bundled contract template by short key
  /// (`token`/`pair`/`router`/`factory`).
  Future<ContractTemplate> template(String key) async {
    final cached = _templates[key];
    if (cached != null) return cached;
    final dir = _templateNames[key];
    if (dir == null) throw ArgumentError('unknown contract template: $key');
    final source = await rootBundle.loadString('$_assetRoot/$dir/contract.py');
    final manifestStr = await rootBundle.loadString('$_assetRoot/$dir/manifest.json');
    final manifest = jsonDecode(manifestStr) as Map<String, dynamic>;
    final tpl = ContractTemplate(
      name: dir,
      codeBytes: Uint8List.fromList(utf8.encode(source)),
      manifestBytes: Uint8List.fromList(utf8.encode(manifestStr)),
      abi: parseAbiFunctions(manifest['abi']),
    );
    _templates[key] = tpl;
    return tpl;
  }

  /// Encode calldata for a method on a template's contract.
  Future<Uint8List> encode(String templateKey, String fn, List<Object?> args) async {
    final tpl = await template(templateKey);
    return encodeCall(tpl.abi, fn, args);
  }

  // ── Read-only views (via state.call) ───────────────────────────────────

  /// Decode a view call's return. Returns null when the contract is missing or
  /// the node has execution disabled (the call reverts).
  Future<Object?> _view(String templateKey, String contract, String fn, List<Object?> args) async {
    final tpl = await template(templateKey);
    final data = await rpc.stateCall(contract: contract, data: encodeCall(tpl.abi, fn, args));
    if (data == null) return null;
    return decodeReturn(tpl.abi, fn, data);
  }

  /// (reserve0, reserve1) for a pair, or null if unreadable.
  Future<(BigInt, BigInt)?> pairReserves(String pairAddr) async {
    final v = await _view('pair', pairAddr, 'reserves', const []);
    if (v is List && v.length == 2) return (v[0] as BigInt, v[1] as BigInt);
    return null;
  }

  Future<int?> pairFeeBps(String pairAddr) async {
    final v = await _view('pair', pairAddr, 'fee_bps', const []);
    return v is BigInt ? v.toInt() : null;
  }

  /// The pair address for (a,b) from the factory registry, or null.
  Future<String?> factoryGetPair(String factory, String tokenA, String tokenB) async {
    final v = await _view('factory', factory, 'get_pair',
        [_tokenArg(tokenA), _tokenArg(tokenB)]);
    if (v is Uint8List && v.any((b) => b != 0)) return _digestToBech32m(v);
    return null;
  }

  Future<int?> factoryPairCount(String factory) async {
    final v = await _view('factory', factory, 'pair_count', const []);
    return v is BigInt ? v.toInt() : null;
  }

  Future<String?> factoryPairAt(String factory, int index) async {
    final v = await _view('factory', factory, 'pair_at', [BigInt.from(index)]);
    if (v is Uint8List && v.any((b) => b != 0)) return _digestToBech32m(v);
    return null;
  }

  // ── Constant-product math (mirrors animica_dex_pair._get_amount_out) ────

  /// Output amount for an exact-input swap, identical to the on-chain formula:
  ///   out = amountIn*(10000-feeBps)*reserveOut
  ///         ── over ── reserveIn*10000 + amountIn*(10000-feeBps)
  static BigInt getAmountOut({
    required BigInt amountIn,
    required BigInt reserveIn,
    required BigInt reserveOut,
    required int feeBps,
  }) {
    if (amountIn <= BigInt.zero || reserveIn <= BigInt.zero || reserveOut <= BigInt.zero) {
      return BigInt.zero;
    }
    final feeFactor = BigInt.from(10000 - feeBps);
    final amountInWithFee = amountIn * feeFactor;
    final numerator = amountInWithFee * reserveOut;
    final denominator = reserveIn * BigInt.from(10000) + amountInWithFee;
    if (denominator <= BigInt.zero) return BigInt.zero;
    return numerator ~/ denominator;
  }

  /// Input amount required for an exact-output swap (rounds up by 1, as the
  /// pair does).
  static BigInt getAmountIn({
    required BigInt amountOut,
    required BigInt reserveIn,
    required BigInt reserveOut,
    required int feeBps,
  }) {
    if (amountOut <= BigInt.zero || amountOut >= reserveOut || reserveIn <= BigInt.zero) {
      return BigInt.zero;
    }
    final feeFactor = BigInt.from(10000 - feeBps);
    final numerator = reserveIn * amountOut * BigInt.from(10000);
    final denominator = (reserveOut - amountOut) * feeFactor;
    if (denominator <= BigInt.zero) return BigInt.zero;
    return (numerator ~/ denominator) + BigInt.one;
  }

  /// Apply a slippage tolerance (in basis points) to a quoted output to get the
  /// on-chain `min_amount_out` guard. MEV.md mandates a strict default.
  static BigInt minOutWithSlippage(BigInt quotedOut, int slippageBps) {
    if (quotedOut <= BigInt.zero) return BigInt.zero;
    return quotedOut * BigInt.from(10000 - slippageBps) ~/ BigInt.from(10000);
  }

  // ── Calldata builders for state-changing DEX/token calls ────────────────

  /// A token-side argument for the router/pair/factory. Native ANM is
  /// identified ON-CHAIN by EMPTY bytes (router `tin == b""`, pair
  /// `_is_native(t) = t == b""`, factory `_sort_tokens`), NOT by a 32-byte
  /// zero address. The UI uses an all-zero printable stand-in for native ANM,
  /// so map any all-zero digest back to `b""`; otherwise the router computes
  /// `expected_native = 0` and every ANM-side swap/liquidity call reverts.
  Uint8List _tokenArg(String addr) {
    final d = decodeAddress(addr).digest;
    return d.every((b) => b == 0) ? Uint8List(0) : d;
  }

  Future<Uint8List> encodeApprove(String spenderDigestOwner, BigInt amount) async {
    // spender is the PAIR address (the pair pulls funds via transfer_from), NOT
    // the router — matches scripts/animica_tokens/chain_ops.py.
    return encode('token', 'approve', [decodeAddress(spenderDigestOwner).digest, amount]);
  }

  Future<Uint8List> encodeTransfer(String toAddr, BigInt amount) =>
      encode('token', 'transfer', [decodeAddress(toAddr).digest, amount]);

  /// router.swap_exact_in(token_in, token_out, amount_in, min_out, to, deadline)
  Future<Uint8List> encodeSwapExactIn({
    required String tokenIn,
    required String tokenOut,
    required BigInt amountIn,
    required BigInt minAmountOut,
    required String to,
    required int deadlineHeight,
  }) =>
      encode('router', 'swap_exact_in', [
        _tokenArg(tokenIn),
        _tokenArg(tokenOut),
        amountIn,
        minAmountOut,
        decodeAddress(to).digest,
        BigInt.from(deadlineHeight),
      ]);

  /// router.add_liquidity(token_a, token_b, a_desired, b_desired, min_lp, deadline)
  Future<Uint8List> encodeAddLiquidity({
    required String tokenA,
    required String tokenB,
    required BigInt amountADesired,
    required BigInt amountBDesired,
    required BigInt minLp,
    required int deadlineHeight,
  }) =>
      encode('router', 'add_liquidity', [
        _tokenArg(tokenA),
        _tokenArg(tokenB),
        amountADesired,
        amountBDesired,
        minLp,
        BigInt.from(deadlineHeight),
      ]);

  /// router.remove_liquidity(token_a, token_b, lp, min_a, min_b, deadline)
  Future<Uint8List> encodeRemoveLiquidity({
    required String tokenA,
    required String tokenB,
    required BigInt lpAmount,
    required BigInt minA,
    required BigInt minB,
    required int deadlineHeight,
  }) =>
      encode('router', 'remove_liquidity', [
        _tokenArg(tokenA),
        _tokenArg(tokenB),
        lpAmount,
        minA,
        minB,
        BigInt.from(deadlineHeight),
      ]);

  /// token.init(...) calldata for the post-deploy initialization call.
  Future<Uint8List> encodeTokenInit(TokenLaunchParams p, String ownerAddr) => encode(
        'token',
        'init',
        [
          Uint8List.fromList(utf8.encode(p.name)),
          Uint8List.fromList(utf8.encode(p.symbol)),
          p.decimals,
          decodeAddress(ownerAddr).digest,
          p.initialSupplyBase,
          p.maxSupplyBase,
          p.mintable,
          Uint8List.fromList(utf8.encode(p.metadataUri)),
          Uint8List(0), // freeze_authority: none
        ],
      );

  // ── Promote a token: pay ANM to the foundation treasury ─────────────────

  /// A promo purchase: [days] featured periods at [PromoConfig.feeAnmPerDay].
  static BigInt promoTotalNanos(int days) =>
      AnimicaConfig.nanosPerAnm * BigInt.from(PromoConfig.feeAnmPerDay) * BigInt.from(days);

  /// The opaque `data` memo attached to a promo transfer. An indexer scanning
  /// foundation-treasury inbound transfers reconstructs the featured set from
  /// these — the same "typed memo in the transfer data bstr" mechanism the App
  /// Store uses (ANMSTORE1). Format:
  ///   ANMPROMO1|<contract-bech32m>|<days>|<label>
  static Uint8List promoMemo({required String contract, required int days, String label = ''}) {
    final safeLabel = label.replaceAll('|', '/').trim();
    final text = 'ANMPROMO1|$contract|$days|$safeLabel';
    return Uint8List.fromList(utf8.encode(text));
  }

  /// Parse a promo memo back out of a transfer's data bstr (indexer/self-check).
  static PromoRecord? parsePromoMemo(Uint8List data) {
    if (data.isEmpty) return null;
    final String text;
    try {
      text = utf8.decode(data);
    } catch (_) {
      return null;
    }
    if (!text.startsWith('ANMPROMO1|')) return null;
    final parts = text.split('|');
    if (parts.length < 3) return null;
    final days = int.tryParse(parts[2]);
    if (days == null || days <= 0) return null;
    return PromoRecord(
      contract: parts[1],
      days: days,
      label: parts.length > 3 ? parts.sublist(3).join('|') : '',
    );
  }

  /// Build the promo transfer body: `days` × daily fee to the foundation
  /// treasury, tagged with [promoMemo]. This is an ordinary t=0 transfer, so it
  /// settles on mainnet today.
  Map<String, dynamic> buildPromoTransfer({
    required String from,
    required String contract,
    required int days,
    required int nonce,
    String label = '',
    int chainId = 1,
  }) {
    if (days <= 0 || days > PromoConfig.maxDays) {
      throw ArgumentError('promo days must be 1..${PromoConfig.maxDays}, got $days');
    }
    return buildTransferBody(
      from: from,
      to: PromoConfig.treasuryAddress,
      amountNanos: promoTotalNanos(days),
      nonce: nonce,
      data: promoMemo(contract: contract, days: days, label: label),
      chainId: chainId,
    );
  }

  // ── Network capability probe ────────────────────────────────────────────

  /// Classify an RPC error as "contract execution is disabled on this node".
  /// The node reverts disabled calls with these markers (see
  /// vm_py/runtime/loader.run_call and execution.runtime.contracts).
  static bool isExecutionDisabled(Object error) {
    final m = error.toString().toLowerCase();
    return m.contains('unsafe_exec') ||
        m.contains('allow_unsafe') ||
        m.contains('execution failed') ||
        m.contains('vm.disabled') ||
        m.contains('vm_runtime_unavailable') ||
        m.contains('call_execution_failed') ||
        (m.contains('revert') && m.contains('disabled'));
  }

  /// Probe whether the connected node executes contracts. Uses the code-
  /// committed marketplace contract's `owner()` view: data back → enabled,
  /// an execution-disabled revert → disabled, anything else → unknown.
  Future<DexCapability> probeExecution() async {
    try {
      // owner() selector, computed against a 1-entry inline ABI.
      final abi = parseAbiFunctions({
        'functions': [
          {'name': 'owner', 'inputs': [], 'outputs': [{'type': 'bytes'}]},
        ],
      });
      final data = await rpc.stateCall(
        contract: AnimicaConfig.probeContract,
        data: encodeCall(abi, 'owner', const []),
      );
      if (data != null && data.isNotEmpty) return DexCapability.enabled;
      return DexCapability.unknown;
    } on RpcError catch (e) {
      if (isExecutionDisabled(e)) return DexCapability.disabled;
      return DexCapability.unknown;
    } catch (_) {
      return DexCapability.unknown;
    }
  }
}

/// A 32-byte contract digest (as returned by a view) → its `anim1…` form.
/// Contract addresses carry alg_id 0x0000 (see address.dart hexToBech32m).
String _digestToBech32m(Uint8List digest) {
  final hex = digest.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return hexToBech32m(hex);
}

class PromoRecord {
  final String contract;
  final int days;
  final String label;
  const PromoRecord({required this.contract, required this.days, this.label = ''});
}

/// Featured-token promotion economics. Priced against the founders-pass anchor
/// (25,000 ANM) so a 24h feature is meaningful but affordable at current ANM
/// value. Override at build time with --dart-define.
class PromoConfig {
  /// ANM charged per 24-hour featured period.
  static const int feeAnmPerDay =
      int.fromEnvironment('ANIMICA_PROMO_FEE_ANM_PER_DAY', defaultValue: 25000);

  /// Upper bound on periods bought at once.
  static const int maxDays = int.fromEnvironment('ANIMICA_PROMO_MAX_DAYS', defaultValue: 30);

  /// Foundation treasury that receives promo deposits.
  static const String treasuryAddress = String.fromEnvironment(
    'ANIMICA_FOUNDATION_TREASURY',
    defaultValue: 'anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga',
  );
}
