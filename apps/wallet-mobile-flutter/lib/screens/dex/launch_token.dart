// Launch a new ANM-20 token. Two on-chain steps: deploy the bundled token
// contract template (this genuinely mines and stores the code today), then
// submit the init call (name/symbol/supply). Init only settles once contract
// execution is enabled on this network — DexCapabilityBanner says so honestly.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../services/rpc.dart';
import '../../state/dex_state.dart';
import '../../state/wallet_state.dart';
import 'dex_common.dart';

enum _LaunchPhase { idle, deploying, initializing, done }

class LaunchTokenScreen extends ConsumerStatefulWidget {
  const LaunchTokenScreen({super.key});

  @override
  ConsumerState<LaunchTokenScreen> createState() => _LaunchTokenScreenState();
}

class _LaunchTokenScreenState extends ConsumerState<LaunchTokenScreen> {
  final _name = TextEditingController();
  final _symbol = TextEditingController();
  final _decimals = TextEditingController(text: '9');
  final _initialSupply = TextEditingController();
  final _maxSupply = TextEditingController();
  final _metadataUri = TextEditingController();
  bool _mintable = false;

  _LaunchPhase _phase = _LaunchPhase.idle;
  bool _busy = false;
  String? _err;
  TxOutcome? _deployed; // set once the deploy tx has been broadcast
  String? _initTxHash;

  @override
  void dispose() {
    _name.dispose();
    _symbol.dispose();
    _decimals.dispose();
    _initialSupply.dispose();
    _maxSupply.dispose();
    _metadataUri.dispose();
    super.dispose();
  }

  /// Validates the form; returns null (with `_err` set) on any problem.
  TokenLaunchParams? _validatedParams() {
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => _err = 'Token name is required.');
      return null;
    }
    final symbol = _symbol.text.trim();
    if (symbol.isEmpty || symbol.length > 12) {
      setState(() => _err = 'Symbol must be 1–12 characters.');
      return null;
    }
    final decimals = int.tryParse(_decimals.text.trim());
    if (decimals == null || decimals < 0 || decimals > 18) {
      setState(() => _err = 'Decimals must be between 0 and 18.');
      return null;
    }
    final supply = BigInt.tryParse(_initialSupply.text.trim());
    if (supply == null || supply <= BigInt.zero) {
      setState(() => _err = 'Initial supply must be a positive whole number.');
      return null;
    }
    BigInt? maxSupply;
    final maxStr = _maxSupply.text.trim();
    if (maxStr.isNotEmpty) {
      maxSupply = BigInt.tryParse(maxStr);
      if (maxSupply == null || maxSupply <= BigInt.zero) {
        setState(() => _err =
            'Max supply must be a positive whole number, or blank for uncapped.');
        return null;
      }
      if (maxSupply < supply) {
        setState(() => _err = 'Max supply cannot be below the initial supply.');
        return null;
      }
    }
    return TokenLaunchParams(
      name: name,
      symbol: symbol,
      decimals: decimals,
      initialSupply: supply,
      maxSupply: maxSupply, // null = uncapped
      mintable: _mintable,
      metadataUri: _metadataUri.text.trim(),
    );
  }

  Future<void> _launch() async {
    setState(() => _err = null);
    final acct = ref.read(activeAccountProvider);
    if (acct == null) {
      setState(
          () => _err = 'No active account. Create or select a wallet first.');
      return;
    }
    final params = _validatedParams();
    if (params == null) return;

    final dex = ref.read(dexServiceProvider);
    final actions = ref.read(dexActionsProvider);

    setState(() => _busy = true);
    try {
      // Step 1: deploy the bundled token contract (skipped on an init retry).
      var deployed = _deployed;
      if (deployed == null) {
        setState(() => _phase = _LaunchPhase.deploying);
        final tpl = await dex.template('token');
        deployed = await actions.deploy(tpl);
        if (!mounted) return;
        setState(() => _deployed = deployed);
      }
      final contract = deployed.contractAddress;
      if (contract == null) {
        throw StateError('The deploy did not yield a contract address.');
      }

      // Step 2: initialize the token at its new address.
      if (!mounted) return;
      setState(() => _phase = _LaunchPhase.initializing);
      final Uint8List initData = await dex.encodeTokenInit(params, acct.address);
      final init = await actions.call(to: contract, calldata: initData);
      if (!mounted) return;
      setState(() {
        _initTxHash = init.txHash;
        _phase = _LaunchPhase.done;
      });
      // Deploy + init both paid fees; nudge the balance after a few seconds.
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted) ref.invalidate(balanceProvider);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _err = e is RpcError ? e.message : e.toString();
        // If the deploy already went out, keep it so the button retries init.
        _phase =
            _deployed == null ? _LaunchPhase.idle : _LaunchPhase.initializing;
      });
      showTxError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final acct = ref.watch(activeAccountProvider);
    final done = _phase == _LaunchPhase.done;
    final editable = !_busy && !done;
    return Scaffold(
      appBar: AppBar(title: const Text('Launch token')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const DexCapabilityBanner(),
          if (acct != null) ...[
            Text('Owner',
                style: theme.textTheme.labelMedium
                    ?.copyWith(color: theme.colorScheme.outline)),
            const SizedBox(height: 4),
            Text(acct.label,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(acct.address,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
            const SizedBox(height: 20),
          ],
          TextField(
            controller: _name,
            enabled: editable,
            decoration: const InputDecoration(
              labelText: 'Name',
              hintText: 'e.g. Animica Dollar',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                flex: 3,
                child: TextField(
                  controller: _symbol,
                  enabled: editable,
                  textCapitalization: TextCapitalization.characters,
                  maxLength: 12,
                  decoration: const InputDecoration(
                    labelText: 'Symbol',
                    hintText: 'e.g. AUSD',
                    border: OutlineInputBorder(),
                    counterText: '',
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                flex: 2,
                child: TextField(
                  controller: _decimals,
                  enabled: editable,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(
                    labelText: 'Decimals',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _initialSupply,
            enabled: editable,
            keyboardType: TextInputType.number,
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: const InputDecoration(
              labelText: 'Initial supply',
              hintText: 'e.g. 1000000',
              helperText: 'Whole tokens — scaled by 10^decimals on-chain.',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _maxSupply,
            enabled: editable,
            keyboardType: TextInputType.number,
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: const InputDecoration(
              labelText: 'Max supply (optional)',
              helperText: 'Blank = uncapped.',
              border: OutlineInputBorder(),
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Mintable'),
            subtitle:
                const Text('Owner can mint more later (up to max supply).'),
            value: _mintable,
            onChanged: editable ? (v) => setState(() => _mintable = v) : null,
          ),
          TextField(
            controller: _metadataUri,
            enabled: editable,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              labelText: 'Metadata URI (optional)',
              hintText: 'ipfs://… or https://… (image, description, links)',
              border: OutlineInputBorder(),
            ),
          ),
          if (_phase != _LaunchPhase.idle) _stepsCard(theme),
          if (_err != null) ...[
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: theme.colorScheme.errorContainer,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(_err!,
                  style: TextStyle(color: theme.colorScheme.onErrorContainer)),
            ),
          ],
          if (_deployed != null) _resultCard(theme),
          const SizedBox(height: 20),
          if (!done)
            FilledButton(
              onPressed: _busy ? null : _launch,
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              child: _busy
                  ? const SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(_deployed == null ? 'Launch' : 'Retry init'),
            ),
        ],
      ),
    );
  }

  Widget _stepsCard(ThemeData theme) {
    final deployDone = _deployed != null;
    final initDone = _initTxHash != null;
    return Card(
      margin: const EdgeInsets.only(top: 16),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _stepRow(
              theme,
              label: deployDone ? 'Contract deployed' : 'Deploying…',
              active: _busy && _phase == _LaunchPhase.deploying,
              complete: deployDone,
            ),
            _stepRow(
              theme,
              label: initDone ? 'Init submitted' : 'Initializing…',
              active: _busy && _phase == _LaunchPhase.initializing,
              complete: initDone,
            ),
          ],
        ),
      ),
    );
  }

  Widget _stepRow(ThemeData theme,
      {required String label, required bool active, required bool complete}) {
    final cs = theme.colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          if (complete)
            Icon(Icons.check_circle, size: 18, color: cs.primary)
          else if (active)
            const SizedBox(
                height: 18,
                width: 18,
                child: CircularProgressIndicator(strokeWidth: 2))
          else
            Icon(Icons.circle_outlined, size: 18, color: cs.outline),
          const SizedBox(width: 10),
          Text(
            label,
            style: TextStyle(
              fontSize: 13,
              color: active || complete ? cs.onSurface : cs.outline,
              fontWeight: active ? FontWeight.w600 : FontWeight.w400,
            ),
          ),
        ],
      ),
    );
  }

  Widget _resultCard(ThemeData theme) {
    final cs = theme.colorScheme;
    final contract = _deployed!.contractAddress;
    final done = _phase == _LaunchPhase.done;
    return Container(
      margin: const EdgeInsets.only(top: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: cs.primaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(done ? '✓ Token launched' : 'Contract deployed',
              style: TextStyle(
                  color: cs.onPrimaryContainer, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          Text('Contract address',
              style: TextStyle(color: cs.onPrimaryContainer, fontSize: 12)),
          const SizedBox(height: 2),
          Row(
            children: [
              Expanded(
                child: SelectableText(contract ?? '(unknown)',
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 11)),
              ),
              IconButton(
                icon: const Icon(Icons.copy, size: 16),
                tooltip: 'Copy address',
                onPressed: contract == null
                    ? null
                    : () => Clipboard.setData(ClipboardData(text: contract)),
              ),
            ],
          ),
          _hashLine(theme, 'Deploy tx', _deployed!.txHash),
          if (_initTxHash != null) _hashLine(theme, 'Init tx', _initTxHash!),
          const SizedBox(height: 8),
          Text(
            done
                ? 'The contract code is mined and stored. Name, symbol and '
                    'supply settle once on-chain contract execution goes live '
                    'on this network.'
                : 'The deploy is out. Initialization has not been submitted '
                    'yet — use the button below to retry.',
            style: TextStyle(color: cs.onPrimaryContainer, fontSize: 12),
          ),
          if (done && contract != null) ...[
            const SizedBox(height: 4),
            Row(
              children: [
                TextButton(
                  onPressed: () => context.push('/dex/token/$contract'),
                  child: const Text('View token'),
                ),
                TextButton(
                  onPressed: () => context.push('/dex/promote/$contract'),
                  child: const Text('Promote'),
                ),
                const Spacer(),
                TextButton(
                  onPressed: () => context.pop(),
                  child: const Text('Done'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _hashLine(ThemeData theme, String label, String hash) {
    final cs = theme.colorScheme;
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        children: [
          Text('$label: ',
              style: TextStyle(color: cs.onPrimaryContainer, fontSize: 12)),
          Text(_shortHash(hash),
              style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
          const SizedBox(width: 6),
          InkWell(
            onTap: () => Clipboard.setData(ClipboardData(text: hash)),
            child: Icon(Icons.copy, size: 14, color: cs.onPrimaryContainer),
          ),
        ],
      ),
    );
  }
}

String _shortHash(String h) =>
    h.length <= 16 ? h : '${h.substring(0, 10)}…${h.substring(h.length - 4)}';
