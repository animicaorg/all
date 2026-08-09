// "Connect to site" — pair this wallet with animica.xyz (or any Animica site
// using the same relay) by scanning the QR code it shows, then approve the
// sign / send requests that site queues.
//
// Nothing is signed without an explicit sheet: the poller only surfaces
// requests, the user accepts or rejects each one, and only then do we touch
// the key. Rejections are reported back to the site as JSON-RPC 4001 so the
// page can show "rejected" rather than hanging until the request expires.

library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../models/account.dart';
import '../services/ml_dsa_65.dart';
import '../services/rpc.dart';
import '../services/signer.dart';
import '../services/wallet_connect.dart';
import '../state/connect_state.dart';
import '../state/wallet_state.dart';

/// Domain prefix the extension, the site verifier and the chain all agree on.
/// Keep byte-identical with wallet-extension `SIGN_MESSAGE_DOMAIN_PREFIX` and
/// animica.xyz `SIGN_DOMAIN` — a mismatch here means every sign-in fails.
const String _kSignMessageDomainPrefix = 'animica:signMessage:';

String _hex(Uint8List bytes) =>
    '0x${bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join()}';

Uint8List? _parseHexBytes(dynamic v) {
  if (v is! String) return null;
  var s = v;
  if (s.startsWith('0x') || s.startsWith('0X')) s = s.substring(2);
  if (s.isEmpty) return Uint8List(0);
  if (s.length.isOdd) return null;
  final out = Uint8List(s.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    final h = int.tryParse(s.substring(i * 2, i * 2 + 2), radix: 16);
    if (h == null) return null;
    out[i] = h;
  }
  return out;
}

BigInt _parseUint(dynamic v) {
  if (v == null) return BigInt.zero;
  if (v is int) return BigInt.from(v);
  if (v is BigInt) return v;
  if (v is String) {
    if (v.startsWith('0x') || v.startsWith('0X')) {
      return BigInt.tryParse(v.substring(2), radix: 16) ?? BigInt.zero;
    }
    return BigInt.tryParse(v) ?? BigInt.zero;
  }
  return BigInt.zero;
}

class ConnectSiteScreen extends ConsumerStatefulWidget {
  /// Pre-filled pairing URI when the screen was opened from a deep link.
  final String? initialUri;
  const ConnectSiteScreen({super.key, this.initialUri});

  @override
  ConsumerState<ConnectSiteScreen> createState() => _ConnectSiteScreenState();
}

class _ConnectSiteScreenState extends ConsumerState<ConnectSiteScreen> {
  final _linkCtrl = TextEditingController();
  bool _handling = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final initial = widget.initialUri;
    if (initial != null && initial.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _handleScanned(initial));
    }
  }

  @override
  void dispose() {
    _linkCtrl.dispose();
    super.dispose();
  }

  Future<void> _handleScanned(String raw) async {
    if (_handling) return;
    WalletConnectUri? pairing;
    try {
      pairing = WalletConnectUri.tryParse(raw);
    } on WalletConnectError catch (e) {
      setState(() => _error = e.message);
      return;
    }
    // Not an Animica pairing code — the scanner keeps looking rather than
    // complaining about every unrelated QR that drifts through the frame.
    if (pairing == null) return;

    setState(() {
      _handling = true;
      _error = null;
    });

    try {
      final account = ref.read(activeAccountProvider);
      if (account == null) {
        throw WalletConnectError('Create or unlock a wallet before connecting to a site.');
      }

      final api = ref.read(walletConnectApiProvider);
      final info = await api.describe(pairing);
      if (info['status'] == 'rejected') {
        throw WalletConnectError('This pairing code was already rejected.');
      }
      if (!mounted) return;

      final approved = await _confirmConnect(
        origin: (info['origin'] as String?) ?? pairing.origin,
        address: account.address,
      );
      if (!approved) {
        await api.reject(pairing);
        if (mounted) Navigator.of(context).maybePop();
        return;
      }

      await api.approve(
        pairing,
        address: account.address,
        publicKeyHex: _hex(account.publicKey),
        algId: account.algId,
        algName: account.algName,
      );

      await ref.read(walletConnectSessionProvider.notifier).set(
            WalletConnectSession(
              sessionId: pairing.sessionId,
              walletSecret: pairing.walletSecret,
              origin: (info['origin'] as String?) ?? pairing.origin,
              apiBase: pairing.apiBase,
              chainId: pairing.chainId,
              address: account.address,
              connectedAt: DateTime.now().toUtc().toIso8601String(),
            ),
          );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Connected. Requests will appear here.')),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = e is WalletConnectError ? e.message : '$e');
    } finally {
      if (mounted) setState(() => _handling = false);
    }
  }

  Future<bool> _confirmConnect({required String origin, required String address}) async {
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (c) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Connect to site?',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              Text(origin, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
              const SizedBox(height: 6),
              const Text(
                'The site will be able to ask you to sign messages and '
                'transactions. Each request still needs your approval.',
              ),
              const SizedBox(height: 14),
              _kv(c, 'Account', address),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.pop(c, false),
                      child: const Text('Reject'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton(
                      onPressed: () => Navigator.pop(c, true),
                      child: const Text('Connect'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    return ok ?? false;
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(walletConnectSessionProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Connect to site')),
      body: session == null ? _buildPairing(context) : _ConnectedView(session: session),
    );
  }

  Widget _buildPairing(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Open animica.xyz on your computer, choose Animica Mobile Wallet, '
          'and scan the QR code it shows.',
        ),
        const SizedBox(height: 16),
        ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: SizedBox(
            height: 280,
            child: MobileScanner(
              onDetect: (capture) {
                for (final barcode in capture.barcodes) {
                  final value = barcode.rawValue;
                  if (value != null && value.isNotEmpty) {
                    _handleScanned(value);
                    break;
                  }
                }
              },
            ),
          ),
        ),
        if (_handling) ...[
          const SizedBox(height: 12),
          const Center(child: CircularProgressIndicator()),
        ],
        if (_error != null) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.errorContainer,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer)),
          ),
        ],
        const SizedBox(height: 24),
        const Text('Or paste a pairing link',
            style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        TextField(
          controller: _linkCtrl,
          decoration: const InputDecoration(
            hintText: 'animica://wc?v=1&sid=...',
            border: OutlineInputBorder(),
          ),
          maxLines: 2,
          minLines: 1,
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () async {
                  final data = await Clipboard.getData(Clipboard.kTextPlain);
                  if (data?.text != null) _linkCtrl.text = data!.text!;
                },
                icon: const Icon(Icons.paste),
                label: const Text('Paste'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FilledButton(
                onPressed: _handling ? null : () => _handleScanned(_linkCtrl.text),
                child: const Text('Connect'),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Connected state: shows the site, and turns queued requests into sheets.
class _ConnectedView extends ConsumerStatefulWidget {
  final WalletConnectSession session;
  const _ConnectedView({required this.session});

  @override
  ConsumerState<_ConnectedView> createState() => _ConnectedViewState();
}

class _ConnectedViewState extends ConsumerState<_ConnectedView> {
  /// Requests already being shown, so a poll tick mid-approval does not stack
  /// a second identical sheet on top of the first.
  final Set<String> _inFlight = {};

  Future<void> _process(WalletConnectRequest request) async {
    if (_inFlight.contains(request.requestId)) return;
    _inFlight.add(request.requestId);

    final session = widget.session;
    final api = ref.read(walletConnectApiProvider);
    final account = ref.read(activeAccountProvider);

    try {
      if (account == null || account.address != session.address) {
        // The session was approved for a specific account; refuse rather than
        // silently signing with whatever is active now.
        await api.resolve(session, request.requestId,
            errorCode: -32000,
            errorMessage: 'The connected account is no longer active in the wallet.');
        return;
      }

      switch (request.kind) {
        case 'signMessage':
          await _handleSignMessage(api, session, request, account);
          break;
        case 'sendTransaction':
          await _handleSendTransaction(api, session, request, account);
          break;
        default:
          await api.resolve(session, request.requestId,
              errorCode: -32601, errorMessage: 'Unsupported request: ${request.kind}');
      }
    } catch (e) {
      await api
          .resolve(session, request.requestId, errorCode: -32603, errorMessage: 'Wallet error: $e')
          .catchError((_) {});
    } finally {
      _inFlight.remove(request.requestId);
    }
  }

  Future<void> _handleSignMessage(
    WalletConnectApi api,
    WalletConnectSession session,
    WalletConnectRequest request,
    Account account,
  ) async {
    final message = request.payload['message'] as String? ?? '';
    if (message.isEmpty) {
      await api.resolve(session, request.requestId,
          errorCode: -32602, errorMessage: 'Empty message');
      return;
    }

    final approved = await _confirmSignMessage(origin: session.origin, message: message);
    if (!approved) {
      await api.resolve(session, request.requestId,
          errorCode: 4001, errorMessage: 'User rejected the signature request.');
      return;
    }

    final signBytes =
        Uint8List.fromList(utf8.encode('$_kSignMessageDomainPrefix$message'));
    final signature = await MlDsa65.sign(account.secretKey, signBytes);

    await api.resolve(session, request.requestId, result: {
      'signature': _hex(signature),
      'publicKey': _hex(account.publicKey),
      'algId': account.algId,
      'algName': account.algName,
    });
  }

  Future<void> _handleSendTransaction(
    WalletConnectApi api,
    WalletConnectSession session,
    WalletConnectRequest request,
    Account account,
  ) async {
    final params = Map<String, dynamic>.from(request.payload['params'] as Map? ?? const {});
    final to = params['to'] as String? ?? '';
    if (to.isEmpty) {
      await api.resolve(session, request.requestId,
          errorCode: -32602, errorMessage: 'Transaction is missing a recipient.');
      return;
    }
    final value = _parseUint(params['value']);
    // Distinguish "no data" (a plain transfer) from "malformed data". Treating
    // an unparseable `data` as absent silently downgrades a contract call to a
    // value transfer — the funds move and the call never happens.
    final Uint8List? data;
    if (params['data'] == null) {
      data = null;
    } else {
      final parsed = _parseHexBytes(params['data']);
      if (parsed == null) {
        await api.resolve(session, request.requestId,
            errorCode: -32602,
            errorMessage: 'Transaction `data` must be a 0x-prefixed hex '
                'string with an even number of digits.');
        return;
      }
      data = parsed;
    }
    // A contract call cannot carry ANM (see buildCallBody's VALUE POLICY).
    if (data != null && data.isNotEmpty && value != BigInt.zero) {
      await api.resolve(session, request.requestId,
          errorCode: -32602,
          errorMessage: 'A contract call cannot carry a value: the canonical '
              'call payload is {to, data} with no amount field. Send the ANM '
              'in a separate transfer.');
      return;
    }

    final approved = await _confirmSend(
      origin: session.origin,
      from: account.address,
      to: to,
      valueNanos: value,
      data: data,
      description: request.payload['description'] as String?,
    );
    if (!approved) {
      await api.resolve(session, request.requestId,
          errorCode: 4001, errorMessage: 'User rejected the transaction.');
      return;
    }

    final rpc = ref.read(rpcProvider);
    // Chain identity the signature is bound to (cached per RPC client) — a
    // signature without it is rejected by the node, so never sign blind.
    final AnimicaChainContext ctx;
    try {
      ctx = await chainContextFor(rpc);
    } on RpcError catch (e) {
      await api.resolve(session, request.requestId,
          errorCode: -32603, errorMessage: e.message);
      return;
    }
    // The remote site's `nonce` is ignored (extension parity): a wrong guess
    // strands the tx or collides with one the wallet already sent.
    final nonce = await rpc.getPendingNonce(account.address);
    final Map<String, dynamic> body;
    if (data != null && data.isNotEmpty) {
      body = buildCallBody(
        from: account.address,
        to: to,
        calldata: data,
        nonce: nonce,
        chainId: ctx.chainId,
      );
    } else {
      body = buildTransferBody(
        from: account.address,
        to: to,
        amountNanos: value,
        nonce: nonce,
        chainId: ctx.chainId,
      );
    }

    final hash = await signAndBroadcast(
      rpc: rpc,
      account: account,
      body: body,
      chainContext: ctx,
    );
    await api.resolve(session, request.requestId, result: {'txHash': hash});
  }

  Future<bool> _confirmSignMessage({required String origin, required String message}) async {
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (c) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Sign message',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text('Requested by $origin',
                  style: TextStyle(color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              const SizedBox(height: 14),
              Container(
                constraints: const BoxConstraints(maxHeight: 220),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(c).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SingleChildScrollView(
                  child: SelectableText(message, style: const TextStyle(fontSize: 12)),
                ),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.pop(c, false),
                      child: const Text('Reject'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton(
                      onPressed: () => Navigator.pop(c, true),
                      child: const Text('Sign'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    return ok ?? false;
  }

  Future<bool> _confirmSend({
    required String origin,
    required String from,
    required String to,
    required BigInt valueNanos,
    required Uint8List? data,
    String? description,
  }) async {
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (c) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Sign transaction',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text('Requested by $origin',
                  style: TextStyle(color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              if (description != null && description.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(description),
              ],
              const SizedBox(height: 14),
              _kv(c, 'From', from),
              _kv(c, 'To', to),
              _kv(c, 'Amount', '${formatAnm(valueNanos)} ANM'),
              if (data != null && data.isNotEmpty)
                _kv(c, 'Data', '${data.length} bytes'),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.pop(c, false),
                      child: const Text('Reject'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton(
                      onPressed: () => Navigator.pop(c, true),
                      child: const Text('Sign + send'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    return ok ?? false;
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final pending = ref.watch(pendingWalletRequestsProvider);

    // Surface each new request as soon as the poller reports it.
    pending.whenData((requests) {
      for (final request in requests) {
        WidgetsBinding.instance.addPostFrameCallback((_) => _process(request));
      }
    });

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: ListTile(
            leading: const Icon(Icons.link, color: Colors.green),
            title: Text(session.origin),
            subtitle: Text(
              'Connected as ${session.address}',
              style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
            ),
          ),
        ),
        const SizedBox(height: 12),
        pending.when(
          data: (requests) => requests.isEmpty
              ? const ListTile(
                  leading: Icon(Icons.hourglass_empty),
                  title: Text('Waiting for requests'),
                  subtitle: Text('Approvals from the site will appear here.'),
                )
              : Column(
                  children: [
                    for (final r in requests)
                      ListTile(
                        leading: const Icon(Icons.pending_actions),
                        title: Text(r.kind == 'signMessage' ? 'Sign message' : 'Send transaction'),
                        subtitle: const Text('Awaiting your approval'),
                      ),
                  ],
                ),
          loading: () => const Center(child: Padding(
            padding: EdgeInsets.all(16),
            child: CircularProgressIndicator(),
          )),
          error: (e, _) => ListTile(
            leading: const Icon(Icons.error_outline),
            title: const Text('Cannot reach the site'),
            subtitle: Text('$e'),
          ),
        ),
        const SizedBox(height: 24),
        OutlinedButton.icon(
          onPressed: () async {
            await ref.read(walletConnectSessionProvider.notifier).clear();
          },
          icon: const Icon(Icons.link_off),
          label: const Text('Disconnect'),
        ),
      ],
    );
  }
}

Widget _kv(BuildContext c, String k, String v) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 72,
            child: Text(k, style: TextStyle(color: Theme.of(c).colorScheme.outline, fontSize: 12)),
          ),
          Expanded(
            child: Text(v, style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
          ),
        ],
      ),
    );
