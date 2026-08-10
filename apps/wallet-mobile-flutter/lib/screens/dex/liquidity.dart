// Add / remove AMM liquidity. Add approves both tokens to the pair (the pair
// pulls funds via transfer_from), then calls router.add_liquidity; Remove
// calls router.remove_liquidity. v1 keeps min LP / min amounts at 0 and uses a
// far-future block deadline. All amounts assume 9 token decimals.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../constants.dart';
import '../../services/rpc.dart';
import '../../state/dex_state.dart';
import 'dex_common.dart';

class LiquidityScreen extends ConsumerStatefulWidget {
  const LiquidityScreen({super.key});

  @override
  ConsumerState<LiquidityScreen> createState() => _LiquidityScreenState();
}

class _LiquidityScreenState extends ConsumerState<LiquidityScreen> {
  // Add tab.
  final _addTokenA = TextEditingController();
  final _addTokenB = TextEditingController();
  final _addAmountA = TextEditingController();
  final _addAmountB = TextEditingController();
  final _addPair = TextEditingController();
  String? _addErr;

  // Remove tab.
  final _remTokenA = TextEditingController();
  final _remTokenB = TextEditingController();
  final _remLp = TextEditingController();
  String? _remErr;

  bool _busy = false;
  String? _stage;

  // Reserves for the pair typed into the Add tab.
  String? _reservesPair;
  (BigInt, BigInt)? _reserves;
  int? _feeBps;
  bool _reservesLoading = false;
  bool _reservesUnavailable = false;

  /// Far-future block height; v1 has no live-head lookup for deadlines.
  static const int _deadlineHeight = 1 << 30;

  @override
  void dispose() {
    _addTokenA.dispose();
    _addTokenB.dispose();
    _addAmountA.dispose();
    _addAmountB.dispose();
    _addPair.dispose();
    _remTokenA.dispose();
    _remTokenB.dispose();
    _remLp.dispose();
    super.dispose();
  }

  bool _looksLikeAddress(String s) => s.startsWith('anim1') && s.length >= 30;

  String _short(String a) =>
      a.length <= 16 ? a : '${a.substring(0, 10)}…${a.substring(a.length - 4)}';

  /// Decimal string → base units, assuming 9 decimals (rejects > 9 dp).
  BigInt _toBaseUnits(String s) {
    final cleaned = s.trim();
    final dot = cleaned.indexOf('.');
    if (dot < 0) return BigInt.parse(cleaned) * AnimicaConfig.nanosPerAnm;
    final whole = cleaned.substring(0, dot);
    var frac = cleaned.substring(dot + 1);
    if (frac.isEmpty || frac.length > 9) {
      throw const FormatException('too many decimals');
    }
    frac = frac.padRight(9, '0');
    final w = whole.isEmpty ? BigInt.zero : BigInt.parse(whole);
    return w * AnimicaConfig.nanosPerAnm + BigInt.parse(frac);
  }

  Future<void> _loadReserves(String pair) async {
    setState(() {
      _reservesPair = pair;
      _reservesLoading = true;
      _reservesUnavailable = false;
      _reserves = null;
      _feeBps = null;
    });
    try {
      final dex = ref.read(dexServiceProvider);
      final res = await dex.pairReserves(pair);
      int? fee;
      if (res != null) fee = await dex.pairFeeBps(pair);
      if (!mounted || _reservesPair != pair) return;
      setState(() {
        _reserves = res;
        _feeBps = fee;
        _reservesUnavailable = res == null;
        _reservesLoading = false;
      });
    } catch (_) {
      if (!mounted || _reservesPair != pair) return;
      setState(() {
        _reservesUnavailable = true;
        _reservesLoading = false;
      });
    }
  }

  void _onPairChanged(String v) {
    final pair = v.trim();
    if (_looksLikeAddress(pair)) {
      _loadReserves(pair);
    } else {
      setState(() {
        _reservesPair = null;
        _reserves = null;
        _feeBps = null;
        _reservesLoading = false;
        _reservesUnavailable = false;
      });
    }
  }

  Future<void> _submitAdd() async {
    final tokenA = _addTokenA.text.trim();
    final tokenB = _addTokenB.text.trim();
    final pair = _addPair.text.trim();
    setState(() => _addErr = null);
    if (!_looksLikeAddress(tokenA) || !_looksLikeAddress(tokenB)) {
      setState(() => _addErr = 'Enter both token contract addresses (anim1…).');
      return;
    }
    if (tokenA == tokenB) {
      setState(() => _addErr = 'Token A and token B must be different.');
      return;
    }
    if (!_looksLikeAddress(pair)) {
      setState(() => _addErr =
          'Enter the pair address — both approvals go to the pair, which '
          'pulls the tokens via transfer_from.');
      return;
    }
    final BigInt amountA;
    final BigInt amountB;
    try {
      amountA = _toBaseUnits(_addAmountA.text);
      amountB = _toBaseUnits(_addAmountB.text);
    } catch (_) {
      setState(() =>
          _addErr = 'Amounts must be positive numbers with at most 9 decimals.');
      return;
    }
    if (amountA <= BigInt.zero || amountB <= BigInt.zero) {
      setState(() => _addErr = 'Amounts must be greater than zero.');
      return;
    }

    setState(() {
      _busy = true;
      _stage = 'Approving token A…';
    });
    try {
      final dex = ref.read(dexServiceProvider);
      final actions = ref.read(dexActionsProvider);

      final Uint8List approveA = await dex.encodeApprove(pair, amountA);
      await actions.call(to: tokenA, calldata: approveA);
      if (!mounted) return;
      setState(() => _stage = 'Approving token B…');

      final Uint8List approveB = await dex.encodeApprove(pair, amountB);
      await actions.call(to: tokenB, calldata: approveB);
      if (!mounted) return;
      setState(() => _stage = 'Adding liquidity…');

      final Uint8List calldata = await dex.encodeAddLiquidity(
        tokenA: tokenA,
        tokenB: tokenB,
        amountADesired: amountA,
        amountBDesired: amountB,
        minLp: BigInt.zero,
        deadlineHeight: _deadlineHeight,
      );
      final outcome = await actions.call(
        to: AnimicaConfig.dexRouterAddress,
        calldata: calldata,
      );
      if (!mounted) return;
      showTxResult(context, outcome);
      _loadReserves(pair); // best-effort refresh
    } catch (e) {
      if (!mounted) return;
      showTxError(context, e);
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _stage = null;
        });
      }
    }
  }

  Future<void> _submitRemove() async {
    final tokenA = _remTokenA.text.trim();
    final tokenB = _remTokenB.text.trim();
    setState(() => _remErr = null);
    if (!_looksLikeAddress(tokenA) || !_looksLikeAddress(tokenB)) {
      setState(
          () => _remErr = 'Enter the pair\'s two token addresses (anim1…).');
      return;
    }
    if (tokenA == tokenB) {
      setState(() => _remErr = 'Token A and token B must be different.');
      return;
    }
    final BigInt lp;
    try {
      lp = _toBaseUnits(_remLp.text);
    } catch (_) {
      setState(() => _remErr =
          'LP amount must be a positive number with at most 9 decimals.');
      return;
    }
    if (lp <= BigInt.zero) {
      setState(() => _remErr = 'LP amount must be greater than zero.');
      return;
    }

    setState(() {
      _busy = true;
      _stage = 'Removing liquidity…';
    });
    try {
      final dex = ref.read(dexServiceProvider);
      final actions = ref.read(dexActionsProvider);
      final Uint8List calldata = await dex.encodeRemoveLiquidity(
        tokenA: tokenA,
        tokenB: tokenB,
        lpAmount: lp,
        minA: BigInt.zero,
        minB: BigInt.zero,
        deadlineHeight: _deadlineHeight,
      );
      final outcome = await actions.call(
        to: AnimicaConfig.dexRouterAddress,
        calldata: calldata,
      );
      if (!mounted) return;
      showTxResult(context, outcome);
    } catch (e) {
      if (!mounted) return;
      showTxError(context, e);
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _stage = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (AnimicaConfig.dexRouterAddress.isEmpty) {
      final cs = Theme.of(context).colorScheme;
      return Scaffold(
        appBar: AppBar(title: const Text('Liquidity')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.water_drop_outlined, size: 48, color: cs.outline),
                const SizedBox(height: 12),
                const Text('No DEX router on this network',
                    style: TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Text(
                  'The router contract hasn\'t been published for this '
                  'network yet, so liquidity can\'t be managed from the '
                  'wallet. Check back after the DEX contracts are deployed.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: cs.outline, fontSize: 13),
                ),
              ],
            ),
          ),
        ),
      );
    }
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Liquidity'),
          bottom: const TabBar(
            tabs: [Tab(text: 'Add'), Tab(text: 'Remove')],
          ),
        ),
        body: TabBarView(
          children: [_buildAdd(context), _buildRemove(context)],
        ),
      ),
    );
  }

  Widget _buildAdd(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    // If the token index knows token A's ANM pair, offer it for the pair field.
    String? suggestedPair;
    final tokenAText = _addTokenA.text.trim();
    if (_addPair.text.trim().isEmpty && _looksLikeAddress(tokenAText)) {
      final info = ref.watch(tokenInfoProvider(tokenAText)).value;
      final p = info?.pairAddress;
      if (p != null && p.isNotEmpty) suggestedPair = p;
    }

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const DexCapabilityBanner(),
        TextField(
          controller: _addTokenA,
          onChanged: (_) => setState(() {}),
          decoration: const InputDecoration(
            labelText: 'Token A address',
            hintText: 'anim1…',
            helperText: 'Token contract (ANM trades as a token on the DEX)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _addTokenB,
          decoration: const InputDecoration(
            labelText: 'Token B address',
            hintText: 'anim1…',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _addAmountA,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                ],
                decoration: const InputDecoration(
                  labelText: 'Amount A',
                  border: OutlineInputBorder(),
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: TextField(
                controller: _addAmountB,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                ],
                decoration: const InputDecoration(
                  labelText: 'Amount B',
                  border: OutlineInputBorder(),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        Text('Amounts assume 9 token decimals (v1).',
            style: TextStyle(color: cs.outline, fontSize: 12)),
        const SizedBox(height: 12),
        TextField(
          controller: _addPair,
          onChanged: _onPairChanged,
          decoration: const InputDecoration(
            labelText: 'Pair address',
            hintText: 'anim1…',
            helperText:
                'Approvals go to the pair — it pulls funds via transfer_from',
            border: OutlineInputBorder(),
          ),
        ),
        if (suggestedPair != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: ActionChip(
                avatar: const Icon(Icons.link, size: 16),
                label: Text('Use indexed pair ${_short(suggestedPair)}'),
                onPressed: () {
                  final p = suggestedPair!;
                  setState(() => _addPair.text = p);
                  _loadReserves(p);
                },
              ),
            ),
          ),
        _reservesSection(context),
        const StatRow(label: 'Min LP out', value: '0', sub: 'v1 — no guard'),
        StatRow(
            label: 'Deadline',
            value: 'block ${_deadlineHeight.toString()}',
            sub: 'far future'),
        if (_addErr != null) _errorBox(context, _addErr!),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: _busy ? null : _submitAdd,
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: _busyChild('Approve & add liquidity'),
        ),
        const SizedBox(height: 8),
        Text(
          'Sends three transactions: approve token A to the pair, approve '
          'token B to the pair, then router.add_liquidity.',
          style: TextStyle(color: cs.outline, fontSize: 12),
        ),
      ],
    );
  }

  Widget _buildRemove(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const DexCapabilityBanner(),
        TextField(
          controller: _remTokenA,
          decoration: const InputDecoration(
            labelText: 'Token A address',
            hintText: 'anim1…',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _remTokenB,
          decoration: const InputDecoration(
            labelText: 'Token B address',
            hintText: 'anim1…',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _remLp,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [
            FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
          ],
          decoration: const InputDecoration(
            labelText: 'LP amount',
            helperText: 'Assumes 9 decimals (v1)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        const StatRow(label: 'Min token A', value: '0', sub: 'v1 — no guard'),
        const StatRow(label: 'Min token B', value: '0', sub: 'v1 — no guard'),
        if (_remErr != null) _errorBox(context, _remErr!),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: _busy ? null : _submitRemove,
          style: FilledButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14)),
          child: _busyChild('Remove liquidity'),
        ),
        const SizedBox(height: 8),
        Text(
          'Calls router.remove_liquidity; the pair burns your LP units and '
          'returns both tokens.',
          style: TextStyle(color: cs.outline, fontSize: 12),
        ),
      ],
    );
  }

  Widget _busyChild(String label) {
    if (!_busy) return Text(label);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(
            height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2)),
        const SizedBox(width: 10),
        Text(_stage ?? 'Working…'),
      ],
    );
  }

  Widget _reservesSection(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (!_looksLikeAddress(_addPair.text.trim())) {
      return const SizedBox.shrink();
    }
    final Widget child;
    if (_reservesLoading) {
      child = const Row(
        children: [
          SizedBox(
              height: 16,
              width: 16,
              child: CircularProgressIndicator(strokeWidth: 2)),
          SizedBox(width: 10),
          Text('Reading pool reserves…'),
        ],
      );
    } else if (_reserves != null) {
      child = Column(
        children: [
          StatRow(label: 'Reserve (token0)', value: formatAnm(_reserves!.$1)),
          StatRow(label: 'Reserve (token1)', value: formatAnm(_reserves!.$2)),
          if (_feeBps != null)
            StatRow(
                label: 'Swap fee',
                value: '${(_feeBps! / 100).toStringAsFixed(2)}%'),
        ],
      );
    } else if (_reservesUnavailable) {
      child = Text('Pool data unavailable on this network yet',
          style: TextStyle(color: cs.outline));
    } else {
      return const SizedBox.shrink();
    }
    return Card(
      margin: const EdgeInsets.only(top: 12, bottom: 4),
      child: Padding(padding: const EdgeInsets.all(12), child: child),
    );
  }

  Widget _errorBox(BuildContext context, String msg) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.errorContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(msg, style: TextStyle(color: cs.onErrorContainer)),
    );
  }
}
