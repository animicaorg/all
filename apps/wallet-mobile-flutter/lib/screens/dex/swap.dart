// Swap ANM ↔ token through the DEX router.
//
// Quotes are computed locally from the pair's on-chain reserves with the same
// constant-product formula the pair contract uses. When the reserves can't be
// read (no pool yet, or the node has contract execution disabled — the current
// mainnet state), the screen says so honestly, disables the live quote, and
// lets the user type the minimum output themselves so the tx can still be
// built and submitted.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../constants.dart';
import '../../services/price.dart';
import '../../services/rpc.dart';
import '../../state/dex_state.dart';
import '../../state/wallet_state.dart';
import 'dex_common.dart';

/// On-chain, the router/pair mark the native-ANM side of a pool with EMPTY
/// bytes. bech32m cannot print an empty payload, so the wallet uses the
/// all-zero address as its printable stand-in for "native ANM" when building
/// router calldata.
const String _kNativeAnmAddress =
    'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3cshhr';

/// Pair fee assumed when the pool's `fee_bps` view is unreadable (the
/// factory's default swap fee).
const int _kDefaultFeeBps = 30;

/// Far-future block height used as the swap deadline; v1 has no cheap
/// current-head read at quote time.
const int _kDeadlineHeight = 1 << 30;

class SwapScreen extends ConsumerStatefulWidget {
  /// Token contract address to swap against ANM, prefilled when opened from a
  /// token page ('/dex/swap?token=…').
  final String? initialToken;
  const SwapScreen({super.key, this.initialToken});

  @override
  ConsumerState<SwapScreen> createState() => _SwapScreenState();
}

class _SwapScreenState extends ConsumerState<SwapScreen> {
  late final TextEditingController _token =
      TextEditingController(text: widget.initialToken ?? '');
  final _fromAmount = TextEditingController();
  final _toAmount = TextEditingController();
  final _decimalsOverride = TextEditingController(text: '9');

  /// true: ANM → token (buy). false: token → ANM (sell, needs approve).
  bool _anmIn = true;
  int _slippageBps = 50;

  // Pool state for the currently selected token.
  TokenInfo? _info;
  String? _poolRequestKey; // pair address a reserves read was scheduled for
  (BigInt, BigInt)? _reserves; // (reserve0 = ANM side, reserve1 = token side)
  int? _feeBps;
  bool _poolChecked = false;
  BigInt? _quotedOut;

  bool _busy = false;
  String? _err;
  TxOutcome? _outcome;

  @override
  void dispose() {
    _token.dispose();
    _fromAmount.dispose();
    _toAmount.dispose();
    _decimalsOverride.dispose();
    super.dispose();
  }

  String get _tokenAddr => _token.text.trim();
  bool get _tokenLooksValid =>
      _tokenAddr.startsWith('anim1') && _tokenAddr.length >= 30;

  int get _tokenDecimals =>
      _info?.decimals ?? int.tryParse(_decimalsOverride.text.trim()) ?? 9;
  String get _tokenSymbol {
    final s = _info?.symbol ?? '';
    return s.isEmpty ? 'TOKEN' : s;
  }

  int get _inDecimals => _anmIn ? 9 : _tokenDecimals;
  int get _outDecimals => _anmIn ? _tokenDecimals : 9;

  // ── Amount math (base units, no doubles) ────────────────────────────────

  /// Decimal string → base units. Rejects more than [decimals] fraction
  /// digits instead of silently rounding.
  BigInt _toBaseUnits(String s, int decimals) {
    final cleaned = s.trim();
    if (cleaned.isEmpty) throw const FormatException('empty amount');
    final scale = BigInt.from(10).pow(decimals);
    final dot = cleaned.indexOf('.');
    if (dot < 0) return BigInt.parse(cleaned) * scale;
    final whole = cleaned.substring(0, dot);
    var frac = cleaned.substring(dot + 1);
    if (frac.length > decimals) {
      throw FormatException('more than $decimals decimals');
    }
    frac = frac.padRight(decimals, '0');
    final wholePart = whole.isEmpty ? BigInt.zero : BigInt.parse(whole);
    final fracPart = frac.isEmpty ? BigInt.zero : BigInt.parse(frac);
    return wholePart * scale + fracPart;
  }

  /// Base units → trimmed decimal string.
  String _fromBaseUnits(BigInt v, int decimals) {
    if (decimals <= 0) return v.toString();
    final scale = BigInt.from(10).pow(decimals);
    final whole = v ~/ scale;
    var frac = (v % scale).toString().padLeft(decimals, '0');
    frac = frac.replaceFirst(RegExp(r'0+$'), '');
    return frac.isEmpty ? '$whole' : '$whole.$frac';
  }

  // ── Pool reserves + live quote ──────────────────────────────────────────

  void _resetPool() {
    _poolRequestKey = null;
    _reserves = null;
    _feeBps = null;
    _poolChecked = false;
    _quotedOut = null;
    _toAmount.clear();
  }

  Future<void> _loadPool(String pair) async {
    final dex = ref.read(dexServiceProvider);
    (BigInt, BigInt)? reserves;
    int? fee;
    try {
      reserves = await dex.pairReserves(pair);
      if (reserves != null) fee = await dex.pairFeeBps(pair);
    } catch (_) {
      reserves = null; // unreadable == unavailable; never guess numbers
    }
    if (!mounted || _poolRequestKey != pair) return; // stale token selection
    setState(() {
      _reserves = reserves;
      _feeBps = fee;
      _poolChecked = true;
      _recalcQuote();
    });
  }

  /// Recompute the quoted output from the current reserves + "from" amount.
  /// Callers wrap this in setState.
  void _recalcQuote() {
    final r = _reserves;
    if (r == null) {
      _quotedOut = null;
      return;
    }
    BigInt amountIn;
    try {
      amountIn = _toBaseUnits(_fromAmount.text, _inDecimals);
    } catch (_) {
      _quotedOut = null;
      _toAmount.clear();
      return;
    }
    if (amountIn <= BigInt.zero) {
      _quotedOut = null;
      _toAmount.clear();
      return;
    }
    // The factory sorts pair tokens lexicographically and the native marker
    // (empty bytes) sorts before any 32-byte token id, so reserve0 is always
    // the ANM side of an ANM/token pool.
    final reserveIn = _anmIn ? r.$1 : r.$2;
    final reserveOut = _anmIn ? r.$2 : r.$1;
    final out = DexService.getAmountOut(
      amountIn: amountIn,
      reserveIn: reserveIn,
      reserveOut: reserveOut,
      feeBps: _feeBps ?? _kDefaultFeeBps,
    );
    if (out <= BigInt.zero) {
      _quotedOut = null;
      _toAmount.clear();
      return;
    }
    _quotedOut = out;
    _toAmount.text = _fromBaseUnits(out, _outDecimals);
  }

  void _flip() {
    setState(() {
      _anmIn = !_anmIn;
      final from = _fromAmount.text;
      _fromAmount.text = _toAmount.text;
      _toAmount.text = from;
      _recalcQuote();
    });
  }

  Future<void> _pasteToken() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    if (!mounted) return;
    final text = data?.text?.trim();
    if (text == null || text.isEmpty) return;
    setState(() {
      _token.text = text;
      _info = null;
      _resetPool();
    });
  }

  // ── Submit ──────────────────────────────────────────────────────────────

  Future<void> _swap() async {
    setState(() {
      _err = null;
      _outcome = null;
    });
    final acct = ref.read(activeAccountProvider);
    if (acct == null) {
      setState(() => _err = 'No active account.');
      return;
    }
    if (AnimicaConfig.dexRouterAddress.isEmpty) {
      setState(() => _err = 'No DEX router is configured for this network.');
      return;
    }
    final tokenAddr = _tokenAddr;
    if (!_tokenLooksValid) {
      setState(() => _err = 'Enter the token\'s anim1… contract address.');
      return;
    }

    final BigInt amountIn;
    try {
      amountIn = _toBaseUnits(_fromAmount.text, _inDecimals);
    } catch (_) {
      setState(
          () => _err = 'Enter a valid amount (max $_inDecimals decimals).');
      return;
    }
    if (amountIn <= BigInt.zero) {
      setState(() => _err = 'Amount must be positive.');
      return;
    }

    // Slippage guard. With a live quote it derives from the quote; without
    // one the user's own "To" figure is the only honest basis.
    final BigInt minOut;
    final quoted = _quotedOut;
    if (quoted != null) {
      minOut = DexService.minOutWithSlippage(quoted, _slippageBps);
    } else {
      BigInt expected;
      try {
        expected = _toBaseUnits(_toAmount.text, _outDecimals);
      } catch (_) {
        expected = BigInt.zero;
      }
      if (expected <= BigInt.zero) {
        setState(() => _err =
            'No live quote on this network — enter the minimum output you '
            'will accept in the "To" field.');
        return;
      }
      minOut = DexService.minOutWithSlippage(expected, _slippageBps);
    }

    // Selling a token: the pair pulls funds via transfer_from, so it (not the
    // router) must be approved first — which needs the pair address.
    final pair = _info?.pairAddress;
    if (!_anmIn && pair == null) {
      setState(() => _err =
          'Selling this token needs its ANM pair address for the approve '
          'step, and it isn\'t known yet on this network.');
      return;
    }

    setState(() => _busy = true);
    try {
      final dex = ref.read(dexServiceProvider);
      final actions = ref.read(dexActionsProvider);

      if (!_anmIn) {
        final Uint8List approveData = await dex.encodeApprove(pair!, amountIn);
        await actions.call(to: tokenAddr, calldata: approveData);
        if (!mounted) return;
      }

      final Uint8List swapData = await dex.encodeSwapExactIn(
        tokenIn: _anmIn ? _kNativeAnmAddress : tokenAddr,
        tokenOut: _anmIn ? tokenAddr : _kNativeAnmAddress,
        amountIn: amountIn,
        minAmountOut: minOut,
        to: acct.address,
        deadlineHeight: _kDeadlineHeight,
      );
      final outcome = await actions.call(
        to: AnimicaConfig.dexRouterAddress,
        calldata: swapData,
        // The router enforces native value == amount_in when ANM is the
        // input side, and exactly 0 otherwise.
        value: _anmIn ? amountIn : null,
      );

      // Best-effort balance refresh once the tx has had a moment to land.
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted) ref.invalidate(balanceProvider);
      });
      if (!mounted) return;
      setState(() => _outcome = outcome);
      showTxResult(context, outcome);
    } on RpcError catch (e) {
      if (!mounted) return;
      setState(() => _err = 'Swap failed: ${e.message}');
    } catch (e) {
      if (!mounted) return;
      setState(() => _err = 'Swap failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ── UI ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    if (AnimicaConfig.dexRouterAddress.isEmpty) {
      return Scaffold(
        appBar: AppBar(title: const Text('Swap')),
        body: const _PoolsNotDeployed(),
      );
    }

    final infoAsync =
        _tokenLooksValid ? ref.watch(tokenInfoProvider(_tokenAddr)) : null;
    _info = infoAsync?.value;
    final market = ref.watch(anmMarketProvider).value;
    final balance = ref.watch(balanceProvider).value;

    // Schedule one reserves read per discovered pair address.
    final pair = _info?.pairAddress;
    if (pair != null && pair != _poolRequestKey) {
      _poolRequestKey = pair;
      Future.microtask(() => _loadPool(pair));
    } else if (pair == null &&
        infoAsync != null &&
        infoAsync.hasValue &&
        !_poolChecked) {
      // The index answered but knows no pool — nothing on-chain to read.
      // Same-build flag flip so the status section below renders correctly.
      _poolChecked = true;
    }

    final hasQuote = _quotedOut != null;
    final fromUnit = _anmIn ? 'ANM' : _tokenSymbol;
    final toUnit = _anmIn ? _tokenSymbol : 'ANM';

    String? fromHelper;
    if (_anmIn) {
      final parts = <String>[];
      if (balance != null) parts.add('Balance ${formatAnm(balance)} ANM');
      if (market != null) {
        try {
          final nanos = _toBaseUnits(_fromAmount.text, 9);
          if (nanos > BigInt.zero) {
            parts.add(
                '≈ ${formatUsd(usdValueOfNanos(nanos, market.usdPerAnm))}');
          }
        } catch (_) {}
      }
      if (parts.isNotEmpty) fromHelper = parts.join('  ·  ');
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Swap')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: ListView(
          children: [
            const DexCapabilityBanner(),

            // Token selection.
            TextField(
              controller: _token,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
              decoration: InputDecoration(
                labelText: 'Token contract',
                hintText: 'anim1…',
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.paste),
                  tooltip: 'Paste',
                  onPressed: _busy ? null : _pasteToken,
                ),
              ),
              onChanged: (_) => setState(() {
                _info = null;
                _resetPool();
              }),
            ),
            if (_info != null) ...[
              const SizedBox(height: 4),
              StatRow(
                label: 'Token',
                value: '${_info!.name} (${_info!.symbol})',
                sub: priceLine(_info!.priceAnm, market),
              ),
            ] else if (_tokenLooksValid &&
                infoAsync != null &&
                infoAsync.isLoading) ...[
              const SizedBox(height: 8),
              const LinearProgressIndicator(minHeight: 2),
            ],
            const SizedBox(height: 16),

            // From / direction / to.
            TextField(
              controller: _fromAmount,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                labelText: 'From',
                suffixText: fromUnit,
                helperText: fromHelper,
                border: const OutlineInputBorder(),
              ),
              onChanged: (_) => setState(_recalcQuote),
            ),
            const SizedBox(height: 8),
            Center(
              child: IconButton.filledTonal(
                icon: const Icon(Icons.swap_vert),
                tooltip: 'Reverse direction',
                onPressed: _busy ? null : _flip,
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _toAmount,
              readOnly: hasQuote,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                labelText:
                    hasQuote ? 'To (estimated)' : 'To (minimum you accept)',
                suffixText: toUnit,
                border: const OutlineInputBorder(),
              ),
              onChanged: hasQuote ? null : (_) => setState(() {}),
            ),
            const SizedBox(height: 16),

            // Slippage.
            Text('Slippage tolerance',
                style:
                    theme.textTheme.labelMedium?.copyWith(color: cs.outline)),
            const SizedBox(height: 6),
            SegmentedButton<int>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: 10, label: Text('0.1%')),
                ButtonSegment(value: 50, label: Text('0.5%')),
                ButtonSegment(value: 100, label: Text('1%')),
              ],
              selected: {_slippageBps},
              onSelectionChanged: (s) => setState(() {
                _slippageBps = s.first;
                _recalcQuote();
              }),
            ),
            if (_tokenLooksValid && _info == null) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _decimalsOverride,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                decoration: const InputDecoration(
                  labelText: 'Token decimals',
                  helperText:
                      'The token index doesn\'t know this token — set its '
                      'decimals manually (most tokens use 9).',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => setState(_recalcQuote),
              ),
            ],
            const SizedBox(height: 16),

            // Pool status / quote details.
            if (_tokenLooksValid && _poolChecked && _reserves == null)
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: cs.secondaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  children: [
                    Icon(Icons.waves_outlined,
                        size: 20, color: cs.onSecondaryContainer),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Pool data unavailable on this network yet. Live '
                        'quotes are off — you can still build and submit the '
                        'swap with your own minimum output.',
                        style: TextStyle(
                            color: cs.onSecondaryContainer, fontSize: 12.5),
                      ),
                    ),
                  ],
                ),
              ),
            if (hasQuote) ...[
              StatRow(
                label: 'Quoted output',
                value:
                    '${_fromBaseUnits(_quotedOut!, _outDecimals)} $toUnit',
              ),
              StatRow(
                label: 'Minimum received',
                value:
                    '${_fromBaseUnits(DexService.minOutWithSlippage(_quotedOut!, _slippageBps), _outDecimals)} $toUnit',
                sub:
                    'after ${(_slippageBps / 100).toStringAsFixed(1)}% slippage',
              ),
              StatRow(
                label: 'Pool fee',
                value:
                    '${((_feeBps ?? _kDefaultFeeBps) / 100).toStringAsFixed(2)}%',
              ),
              if (_reserves != null)
                StatRow(
                  label: 'Pool reserves',
                  value: '${formatAnm(_reserves!.$1)} ANM',
                  sub:
                      '${_fromBaseUnits(_reserves!.$2, _tokenDecimals)} $_tokenSymbol',
                ),
            ],

            if (_err != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: cs.errorContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(_err!,
                    style: TextStyle(color: cs.onErrorContainer)),
              ),
            ],
            if (_outcome != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: cs.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('✓ Submitted',
                        style: TextStyle(
                            color: cs.onPrimaryContainer,
                            fontWeight: FontWeight.w700)),
                    const SizedBox(height: 4),
                    SelectableText(_outcome!.txHash,
                        style: const TextStyle(
                            fontFamily: 'monospace', fontSize: 10)),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        TextButton.icon(
                          icon: const Icon(Icons.copy, size: 16),
                          label: const Text('Copy'),
                          onPressed: () => Clipboard.setData(
                              ClipboardData(text: _outcome!.txHash)),
                        ),
                        const Spacer(),
                        TextButton(
                          onPressed: () => context.pop(),
                          child: const Text('Done'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],

            const SizedBox(height: 20),
            FilledButton(
              onPressed: _busy ? null : _swap,
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              child: _busy
                  ? const SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(_anmIn
                      ? 'Swap ANM → $_tokenSymbol'
                      : 'Swap $_tokenSymbol → ANM'),
            ),
            if (!_anmIn)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Selling first submits an approve to the token, then the '
                  'swap through the router (two transactions).',
                  style: TextStyle(color: cs.outline, fontSize: 12),
                  textAlign: TextAlign.center,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Shown when no router contract is configured for this network: there is
/// nothing to swap against, so be honest instead of guessing an address.
class _PoolsNotDeployed extends StatelessWidget {
  const _PoolsNotDeployed();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.swap_horiz, size: 56, color: cs.outline),
            const SizedBox(height: 16),
            Text('DEX pools not deployed yet',
                style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(
              'This network has no router contract configured, so there is '
              'nothing to swap against yet. You can still launch a token '
              'today — pools follow once the DEX contracts are published.',
              textAlign: TextAlign.center,
              style: TextStyle(color: cs.outline, fontSize: 13),
            ),
            const SizedBox(height: 20),
            OutlinedButton.icon(
              icon: const Icon(Icons.rocket_launch_outlined),
              label: const Text('Launch a token'),
              onPressed: () => context.push('/dex/launch'),
            ),
          ],
        ),
      ),
    );
  }
}
