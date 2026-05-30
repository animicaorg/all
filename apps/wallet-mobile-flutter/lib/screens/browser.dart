// Dapp browser — in-app webview with provider-injection (v0.2).
//
// v0.1: navigation + URL bar + bookmarks for Animica-family sites.
// The `window.animica` injection that surfaces the wallet to dapps
// (animica_requestAccounts, animica_sendTransaction, etc.) is gated on
// the v0.2 send/sign pipeline so we don't surface methods we can't
// fulfill. The injection scaffold is already wired in
// `_buildProviderScript` below — flip `_enableProvider` once the send
// path lands.

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../constants.dart';
import '../state/wallet_state.dart';

const _enableProvider = false;
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
          if (host.endsWith(pattern.substring(0, pattern.length - 1))) return true;
        } else if (host == pattern) {
          return true;
        }
      }
    } catch (_) {}
    return false;
  }

  String _buildProviderScript(String? address) {
    // Inject a stub window.animica that resolves the few read-only calls
    // immediately. Sign/send methods will route through Flutter's
    // JavaScript handler `animicaRequest` and either fulfill or reject.
    // See onWebViewCreated below where we register the handler.
    return '''
      (function() {
        if (window.animica) return;
        var pending = {}, nextId = 1;
        window.animica = {
          isAnimica: true,
          version: '0.1.0',
          selectedAddress: ${address == null ? 'null' : "'$address'"},
          chainId: '0x${AnimicaConfig.chainId.toRadixString(16)}',
          request: function(args) {
            return new Promise(function(resolve, reject) {
              var id = nextId++;
              pending[id] = { resolve: resolve, reject: reject };
              window.flutter_inappwebview.callHandler('animicaRequest', JSON.stringify({
                id: id,
                method: args.method,
                params: args.params
              }));
            });
          },
          _resolve: function(id, value) {
            if (pending[id]) { pending[id].resolve(value); delete pending[id]; }
          },
          _reject: function(id, err) {
            if (pending[id]) { pending[id].reject(err); delete pending[id]; }
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
                return ActionChip(
                  label: Text(b.label),
                  onPressed: () => _go(b.url),
                );
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
                    // v0.2: route requests to native sign/send flow.
                    return {
                      'error': {
                        'code': -32601,
                        'message':
                            'wallet provider not yet enabled in v0.1 mobile build'
                      }
                    };
                  },
                );
              },
              onLoadStop: (c, url) async {
                if (url == null) return;
                final urlString = url.toString();
                _ctrl.text = urlString;
                if (_enableProvider && _isWhitelisted(urlString)) {
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
