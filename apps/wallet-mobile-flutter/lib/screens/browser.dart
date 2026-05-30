// Dapp browser with `window.animica` provider injection.
//
// JavaScript bridge layout:
//
//   page calls window.animica.request({method, params}) → Flutter handler
//     `animicaRequest` receives the JSON, looks at `method`, and either:
//       - answers from cache (animica_accounts, animica_chainId)
//       - shows a native confirmation sheet (animica_requestAccounts,
//         animica_sendTransaction) and resolves on user accept / rejects
//         with code 4001 on user denial
//
//   Resolution is delivered back to the page by evaluating
//     window.animica._resolve(id, value)  /  ._reject(id, err)
//
// Only hosts in AnimicaConfig.walletProviderHosts get the injection.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../constants.dart';
import '../services/rpc.dart';
import '../services/signer.dart';
import '../state/wallet_state.dart';

const _bookmarks = [
  _Bookmark('animica.xyz', 'https://animica.xyz'),
  _Bookmark('Marketplace', 'https://animica.xyz/marketplace'),
  _Bookmark('Founders Pass', 'https://animica.xyz/marketplace/founders'),
  _Bookmark('Buy ANM', 'https://buy.animica.org'),
  _Bookmark('Explorer', 'https://explorer.animica.org'),
  _Bookmark('Pool', 'https://pool.animica.org'),
];

class _Bookmark {
  final String label;
  final String url;
  const _Bookmark(this.label, this.url);
}

class BrowserScreen extends ConsumerStatefulWidget {
  const BrowserScreen({super.key});
  @override
  ConsumerState<BrowserScreen> createState() => _BrowserScreenState();
}

class _BrowserScreenState extends ConsumerState<BrowserScreen> {
  final _ctrl = TextEditingController(text: 'https://animica.xyz');
  InAppWebViewController? _wv;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  bool _isWhitelisted(String url) {
    try {
      final host = Uri.parse(url).host.toLowerCase();
      for (final pattern in AnimicaConfig.walletProviderHosts) {
        if (pattern.endsWith('*')) {
          if (host.endsWith(pattern.substring(0, pattern.length - 1))) {
            return true;
          }
        } else if (host == pattern) {
          return true;
        }
      }
    } catch (_) {}
    return false;
  }

  String _buildProviderScript(String? address) {
    final addrJs = address == null ? 'null' : "'$address'";
    final chainHex = '0x${AnimicaConfig.chainId.toRadixString(16)}';
    return '''
      (function() {
        if (window.animica) return;
        var pending = {}, nextId = 1;
        var handlers = {};
        function emit(event, data) {
          (handlers[event] || []).forEach(function(h) {
            try { h(data); } catch (_) {}
          });
        }
        window.animica = {
          isAnimica: true,
          version: '0.2.0',
          selectedAddress: $addrJs,
          chainId: '$chainHex',
          networkVersion: '${AnimicaConfig.chainId}',
          request: function(args) {
            return new Promise(function(resolve, reject) {
              var id = nextId++;
              pending[id] = { resolve: resolve, reject: reject };
              window.flutter_inappwebview.callHandler(
                'animicaRequest',
                JSON.stringify({ id: id, method: args.method, params: args.params })
              );
            });
          },
          on: function(event, handler) {
            (handlers[event] = handlers[event] || []).push(handler);
          },
          removeListener: function(event, handler) {
            handlers[event] = (handlers[event] || []).filter(function(h) {
              return h !== handler;
            });
          },
          _resolve: function(id, value) {
            if (pending[id]) { pending[id].resolve(value); delete pending[id]; }
          },
          _reject: function(id, code, message) {
            if (pending[id]) {
              var err = new Error(message);
              err.code = code;
              pending[id].reject(err);
              delete pending[id];
            }
          }
        };
        window.dispatchEvent(new Event('animica#initialized'));
      })();
    ''';
  }

  Future<void> _go(String url) async {
    var u = url.trim();
    if (!u.startsWith('http')) u = 'https://$u';
    _ctrl.text = u;
    await _wv?.loadUrl(urlRequest: URLRequest(url: WebUri(u)));
  }

  Future<dynamic> _handleProviderRequest(
      InAppWebViewController controller, Map<String, dynamic> req) async {
    final id = req['id'];
    final method = req['method'] as String? ?? '';
    final params = req['params'];
    final acc = ref.read(activeAccountProvider);

    Future<void> resolve(dynamic value) async {
      final encoded = jsonEncode(value);
      await controller.evaluateJavascript(
        source: 'window.animica._resolve($id, $encoded);',
      );
    }

    Future<void> reject(int code, String message) async {
      final escaped = jsonEncode(message);
      await controller.evaluateJavascript(
        source: 'window.animica._reject($id, $code, $escaped);',
      );
    }

    if (acc == null) {
      await reject(-32000, 'wallet has no active account');
      return null;
    }

    try {
      switch (method) {
        case 'animica_chainId':
          await resolve(
              '0x${AnimicaConfig.chainId.toRadixString(16)}');
          break;
        case 'animica_accounts':
        case 'eth_accounts':
        case 'provider_getAccounts':
          await resolve([acc.address]);
          break;
        case 'animica_requestAccounts':
        case 'eth_requestAccounts':
        case 'provider_requestAccounts':
          final approved = await _confirmConnect(acc.address, _ctrl.text);
          if (approved) {
            await resolve([acc.address]);
          } else {
            await reject(4001, 'User rejected the request.');
          }
          break;
        case 'animica_getBalance':
          {
            final addr = (params is List && params.isNotEmpty)
                ? params.first as String
                : acc.address;
            final bal = await ref.read(rpcProvider).getBalance(addr);
            await resolve(bal.toString());
          }
          break;
        case 'animica_getNonce':
          {
            final addr = (params is List && params.isNotEmpty)
                ? params.first as String
                : acc.address;
            final n = await ref.read(rpcProvider).getPendingNonce(addr);
            await resolve(n);
          }
          break;
        case 'animica_sendTransaction':
        case 'eth_sendTransaction':
        case 'provider_sendTransaction':
          {
            final tx = _firstParam(params);
            if (tx == null) {
              await reject(-32602, 'sendTransaction params required');
              break;
            }
            final approved = await _confirmSend(
              from: acc.address,
              to: tx['to'] as String? ?? '',
              valueNanos: _parseUintParam(tx['value']),
              data: _parseHexBytes(tx['data']),
              hostLabel: _ctrl.text,
            );
            if (!approved) {
              await reject(4001, 'User rejected the transaction.');
              break;
            }
            final rpc = ref.read(rpcProvider);
            final nonce = tx['nonce'] is int
                ? tx['nonce'] as int
                : await rpc.getPendingNonce(acc.address);
            final chainId = await rpc.chainId();
            final data = _parseHexBytes(tx['data']);
            final body = data == null || data.isEmpty
                ? buildTransferBody(
                    from: acc.address,
                    to: tx['to'] as String,
                    amountNanos: _parseUintParam(tx['value']),
                    nonce: nonce,
                    chainId: chainId,
                  )
                : buildCallBody(
                    from: acc.address,
                    to: tx['to'] as String,
                    calldata: data,
                    nonce: nonce,
                    value: _parseUintParam(tx['value']),
                    chainId: chainId,
                  );
            final hash =
                await signAndBroadcast(rpc: rpc, account: acc, body: body);
            await resolve(hash);
          }
          break;
        default:
          await reject(-32601, 'Method not implemented in mobile wallet: $method');
      }
    } catch (e) {
      await reject(-32603, 'wallet error: $e');
    }
    return null;
  }

  Map<String, dynamic>? _firstParam(dynamic params) {
    if (params is List && params.isNotEmpty && params.first is Map) {
      return Map<String, dynamic>.from(params.first as Map);
    }
    if (params is Map) {
      return Map<String, dynamic>.from(params);
    }
    return null;
  }

  BigInt _parseUintParam(dynamic v) {
    if (v == null) return BigInt.zero;
    if (v is int) return BigInt.from(v);
    if (v is BigInt) return v;
    if (v is String) {
      if (v.startsWith('0x') || v.startsWith('0X')) {
        return BigInt.parse(v.substring(2), radix: 16);
      }
      return BigInt.tryParse(v) ?? BigInt.zero;
    }
    return BigInt.zero;
  }

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

  Future<bool> _confirmConnect(String address, String origin) async {
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
              const Text('Connect wallet?',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              Text(origin, style: const TextStyle(fontFamily: 'monospace', fontSize: 11)),
              const SizedBox(height: 6),
              const Text('wants to see your wallet address.'),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(c).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SelectableText(
                  address,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
                ),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => Navigator.pop(c, false),
                      child: const Text('Cancel'),
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

  Future<bool> _confirmSend({
    required String from,
    required String to,
    required BigInt valueNanos,
    required Uint8List? data,
    required String hostLabel,
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
              Text('Requested by $hostLabel',
                  style: TextStyle(
                      color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              const SizedBox(height: 14),
              _kv(c, 'From', from),
              _kv(c, 'To', to),
              _kv(c, 'Amount', '${formatAnm(valueNanos)} ANM'),
              if (data != null && data.isNotEmpty)
                _kv(c, 'Data', '0x${_short(data)}'),
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

  Widget _kv(BuildContext c, String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 64,
              child: Text(k,
                  style: TextStyle(
                      color: Theme.of(c).colorScheme.outline, fontSize: 12)),
            ),
            Expanded(
              child: SelectableText(
                v,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ],
        ),
      );

  String _short(Uint8List b) {
    final h = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
    if (h.length <= 32) return h;
    return '${h.substring(0, 24)}…${h.substring(h.length - 4)}';
  }

  @override
  Widget build(BuildContext context) {
    final acc = ref.watch(activeAccountProvider);
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 8,
        title: TextField(
          controller: _ctrl,
          decoration: const InputDecoration(
            hintText: 'https://…',
            isDense: true,
            border: OutlineInputBorder(),
            contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          ),
          onSubmitted: _go,
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => _wv?.reload(),
          ),
        ],
      ),
      body: Column(
        children: [
          SizedBox(
            height: 44,
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              scrollDirection: Axis.horizontal,
              itemBuilder: (c, i) {
                final b = _bookmarks[i];
                return ActionChip(label: Text(b.label), onPressed: () => _go(b.url));
              },
              separatorBuilder: (_, __) => const SizedBox(width: 8),
              itemCount: _bookmarks.length,
            ),
          ),
          Expanded(
            child: InAppWebView(
              initialUrlRequest: URLRequest(url: WebUri(_ctrl.text)),
              onWebViewCreated: (c) {
                _wv = c;
                c.addJavaScriptHandler(
                  handlerName: 'animicaRequest',
                  callback: (args) async {
                    try {
                      final raw = args.first as String;
                      final parsed = jsonDecode(raw) as Map<String, dynamic>;
                      await _handleProviderRequest(c, parsed);
                    } catch (e) {
                      // Failed to parse — best-effort error back to the page.
                      await c.evaluateJavascript(
                        source:
                            'window.animica && window.animica._reject(0, -32603, ${jsonEncode(e.toString())});',
                      );
                    }
                    return null;
                  },
                );
              },
              onLoadStart: (c, url) async {
                if (url != null) _ctrl.text = url.toString();
                // Inject as early as possible so dapps that probe for
                // `window.animica` at document_start see it.
                final u = url?.toString() ?? '';
                if (_isWhitelisted(u)) {
                  await c.evaluateJavascript(
                      source: _buildProviderScript(acc?.address));
                }
              },
              onLoadStop: (c, url) async {
                if (url == null) return;
                _ctrl.text = url.toString();
                if (_isWhitelisted(url.toString())) {
                  await c.evaluateJavascript(
                      source: _buildProviderScript(acc?.address));
                }
              },
            ),
          ),
        ],
      ),
    );
  }
}
