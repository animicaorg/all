// Dapp browser with `window.animica` provider injection.
//
// JavaScript bridge layout:
//
//   page calls window.animica.request({method, params}) → Flutter handler
//     `animicaRequest` receives the JSON, looks at `method`, and either:
//       - answers from cache (animica_accounts, animica_chainId)
//       - shows a native confirmation sheet (animica_requestAccounts,
//         animica_sendTransaction, animica_deployContract,
//         animica_signMessage, animica_getPublicKey) and resolves on user
//         accept / rejects with code 4001 on user denial
//
// Everything that spends funds, deploys code, or hands the page a signature or
// a public key goes through a sheet. Nothing here auto-approves.
//
//   Resolution is delivered back to the page by evaluating
//     window.animica._resolve(id, value)  /  ._reject(id, err)
//
// Only hosts in AnimicaConfig.walletProviderHosts may use the provider. The
// whitelist is enforced TWICE and the second one is the one that matters: the
// injection is skipped for other hosts, AND `_handleProviderRequest` re-checks
// the loaded page's origin before doing anything, because the
// `flutter_inappwebview` handler is registered on the webview and is reachable
// from any document that calls `flutter_inappwebview.callHandler` itself.
//
// Chain id: the injected `window.animica.chainId` property is the build-time
// constant (it has to be readable synchronously). `animica_chainId` and
// `animica_switchChain` answer from the chain identity the wallet verified and
// signs against — that is the authoritative one.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../constants.dart';
import '../models/account.dart';
import '../services/deploy.dart';
import '../services/ml_dsa_65.dart';
import '../services/rpc.dart';
import '../services/signer.dart';
import '../state/wallet_state.dart';

/// Domain prefix for `animica_signMessage` / `personal_sign`.
///
/// Must stay byte-identical to the extension
/// (apps/wallet-extension/src/core/crypto/sign-domain.ts) and to the
/// server-side verifier (apps/animica-marketplace/lib/wallet-verify.ts), which
/// verifies `ml_dsa65.verify(pk, UTF8(prefix + message), sig)` in pure mode.
/// The same constant is used by the WalletConnect path in connect_site.dart.
const String _kSignMessageDomainPrefix = 'animica:signMessage:';

/// bech32m body charset (no `1`, `b`, `i`, `o`) — used only to tell an address
/// apart from a message in `personal_sign`'s two argument orders.
final RegExp _animAddressRe =
    RegExp(r'^anim1[02-9ac-hj-np-z]+$', caseSensitive: false);

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

/// Normalized `signMessage` / `personal_sign` request.
class _SignMessageRequest {
  final String message;
  final String? from;
  const _SignMessageRequest(this.message, this.from);
}

class BrowserScreen extends ConsumerStatefulWidget {
  const BrowserScreen({super.key, this.initialUrl});

  /// Optional deep-link start page (e.g. the Games tab launching
  /// Thronebound). Falls back to the default homepage when null.
  final String? initialUrl;

  @override
  ConsumerState<BrowserScreen> createState() => _BrowserScreenState();
}

class _BrowserScreenState extends ConsumerState<BrowserScreen> {
  late final _ctrl = TextEditingController(
      text: widget.initialUrl ?? 'https://animica.xyz');
  InAppWebViewController? _wv;

  /// Origin of the page that is actually LOADED, tracked from `onLoadStart`.
  ///
  /// Never read `_ctrl.text` for a security decision: it is a user-editable
  /// text field, it is also what a redirect leaves behind, and it holds a full
  /// URL rather than an origin. Approval sheets show this value too, so what
  /// the user authorizes is what the provider gated on.
  String? _pageOrigin;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  /// `https://host[:port]` for the currently loaded page, or `about:blank`
  /// before the first navigation completes.
  String get _originLabel => _pageOrigin ?? 'about:blank';

  void _setPageOrigin(WebUri? url) {
    if (url == null) {
      _pageOrigin = null;
      return;
    }
    final u = Uri.tryParse(url.toString());
    if (u == null || u.host.isEmpty) {
      _pageOrigin = null;
      return;
    }
    _pageOrigin = u.hasPort
        ? '${u.scheme}://${u.host}:${u.port}'
        : '${u.scheme}://${u.host}';
  }

  bool _isWhitelisted(String url) {
    try {
      final parsed = Uri.parse(url);
      // https only: the whitelist is a host list, so without this an http://
      // downgrade of a listed host (or a MITM on a captive-portal network)
      // would be handed the full provider surface.
      if (parsed.scheme.toLowerCase() != 'https') return false;
      final host = parsed.host.toLowerCase();
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
          version: '0.3.0',
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

    // `window.animica` is only injected into whitelisted pages, so guard the
    // dereference: a rejected request from a non-whitelisted document must
    // still deliver its error instead of throwing inside evaluateJavascript.
    Future<void> resolve(dynamic value) async {
      final encoded = jsonEncode(value);
      await controller.evaluateJavascript(
        source: 'window.animica && window.animica._resolve($id, $encoded);',
      );
    }

    Future<void> reject(int code, String message) async {
      final escaped = jsonEncode(message);
      await controller.evaluateJavascript(
        source:
            'window.animica && window.animica._reject($id, $code, $escaped);',
      );
    }

    // HOST WHITELIST IS THE FIRST CHECK. Only whitelisted pages get the
    // `window.animica` injection, but `flutter_inappwebview` registers the
    // `animicaRequest` JavaScript handler on the WEBVIEW, not on a page: any
    // document that reaches `window.flutter_inappwebview.callHandler(...)`
    // directly — a non-whitelisted site, an iframe, an http→https downgrade —
    // hits this method regardless of injection. Gating only the injection left
    // the whole provider surface (sign, send, deploy, public key) reachable
    // from arbitrary origins.
    if (_pageOrigin == null || !_isWhitelisted(_pageOrigin!)) {
      await reject(4100,
          'This site is not allowed to use the Animica wallet provider.');
      return null;
    }

    if (acc == null) {
      await reject(-32000, 'wallet has no active account');
      return null;
    }

    try {
      switch (method) {
        case 'animica_chainId':
          {
            // Answer from the chain identity the wallet actually signs
            // against, not from the compile-time constant. fetchChainContext
            // rejects a node whose chain id disagrees with this build, so by
            // the time we get a context the two are reconciled.
            final ctx = await chainContextFor(ref.read(rpcProvider));
            await resolve('0x${ctx.chainId.toRadixString(16)}');
          }
          break;
        case 'animica_accounts':
        case 'eth_accounts':
        case 'provider_getAccounts':
          await resolve([acc.address]);
          break;
        case 'animica_requestAccounts':
        case 'eth_requestAccounts':
        case 'provider_requestAccounts':
          final approved = await _confirmConnect(acc.address, _originLabel);
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
        case 'animica_deployContract':
        case 'provider_deployContract':
          {
            final args = _firstParam(params);
            if (args == null) {
              await reject(-32602,
                  'Invalid deploy params: expected { from?, code, manifest, value?, gasLimit?, gasPrice? }');
              break;
            }
            await _deployContract(
              acc: acc,
              args: args,
              resolve: resolve,
              reject: reject,
            );
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
            // Kind inference, same as the extension's handleSendTransaction:
            // code + manifest present means this is a deploy, and a deploy
            // cannot be expressed in the flat transfer/call body at all.
            // Routing it here (instead of silently encoding a value-less
            // transfer) is what makes `sendTransaction`-based launchers work.
            final deployCode = parseDeployBytes(tx['code']);
            final deployManifest = parseDeployBytes(tx['manifest']);
            if ((deployCode?.isNotEmpty ?? false) &&
                (deployManifest?.isNotEmpty ?? false)) {
              await _deployContract(
                acc: acc,
                args: tx,
                resolve: (value) async {
                  // sendTransaction's contract is "resolve with the tx hash",
                  // so unwrap the deploy result.
                  final txid = value is Map ? value['txid'] : value;
                  await resolve(txid);
                },
                reject: reject,
              );
              break;
            }
            final to = tx['to'];
            if (to is! String || to.trim().isEmpty) {
              await reject(-32602, 'sendTransaction requires a `to` address');
              break;
            }
            // Parse `data` ONCE, and distinguish "absent" from "malformed".
            // `_parseHexBytes` returns null for both, and null used to mean
            // "no data" — so `data: "0xzz"` or an odd-length string quietly
            // dropped the calldata and turned a contract call into a plain
            // value transfer. Now malformed input is a hard -32602.
            final Uint8List? data;
            if (!tx.containsKey('data') || tx['data'] == null) {
              data = null;
            } else {
              final parsed = _parseHexBytes(tx['data']);
              if (parsed == null) {
                await reject(-32602,
                    'sendTransaction `data` must be a 0x-prefixed hex string '
                    'with an even number of digits.');
                break;
              }
              data = parsed;
            }
            final value = _parseUintParam(tx['value']);
            final isCall = data != null && data.isNotEmpty;
            // A call cannot carry ANM (see buildCallBody's VALUE POLICY):
            // reject up front rather than after the user has approved.
            if (isCall && value != BigInt.zero) {
              await reject(
                  -32602,
                  'A contract call cannot carry a value: the canonical call '
                  'payload is {to, data} with no amount field. Send the ANM '
                  'in a separate transfer.');
              break;
            }
            final approved = await _confirmSend(
              from: acc.address,
              to: to,
              valueNanos: value,
              data: data,
              hostLabel: _originLabel,
            );
            if (!approved) {
              await reject(4001, 'User rejected the transaction.');
              break;
            }
            final rpc = ref.read(rpcProvider);
            final ctx = await chainContextFor(rpc);
            // A dapp-supplied nonce is advisory at best and a replay/stuck-tx
            // hazard at worst; the extension always reads the pending nonce
            // and so do we.
            final nonce = await rpc.getPendingNonce(acc.address);
            final Map<String, dynamic> body;
            if (data != null && data.isNotEmpty) {
              body = buildCallBody(
                from: acc.address,
                to: to,
                calldata: data,
                nonce: nonce,
                chainId: ctx.chainId,
              );
            } else {
              body = buildTransferBody(
                from: acc.address,
                to: to,
                amountNanos: value,
                nonce: nonce,
                chainId: ctx.chainId,
              );
            }
            final hash = await signAndBroadcast(
              rpc: rpc,
              account: acc,
              body: body,
              chainContext: ctx,
            );
            await resolve(hash);
          }
          break;
        case 'animica_signMessage':
        case 'provider_signMessage':
        case 'personal_sign':
          {
            final req = _parseSignMessageParams(method, params);
            if (req == null) {
              await reject(-32602, 'Invalid signMessage params');
              break;
            }
            final requested = req.from;
            if (requested != null &&
                requested.toLowerCase() != acc.address.toLowerCase()) {
              await reject(-32000,
                  'Signer $requested is not the active wallet account (${acc.address}).');
              break;
            }
            final approved = await _confirmSignMessage(
              origin: _originLabel,
              address: acc.address,
              message: req.message,
            );
            if (!approved) {
              await reject(4001, 'User rejected the signature request.');
              break;
            }
            // Sign the domain-prefixed UTF-8 bytes verbatim — no CBOR, no
            // sha3 prehash. The verifier runs ml_dsa65.verify in pure mode
            // over exactly these bytes.
            final signBytes = Uint8List.fromList(
                utf8.encode('$_kSignMessageDomainPrefix${req.message}'));
            final sig = await MlDsa65.sign(acc.secretKey, signBytes);
            await resolve('0x${_hexAll(sig)}');
          }
          break;
        case 'animica_getPublicKey':
        case 'provider_getPublicKey':
          {
            final requested = _parseAddressParam(params);
            if (requested != null &&
                requested.toLowerCase() != acc.address.toLowerCase()) {
              await reject(-32000,
                  'Address $requested is not the active wallet account (${acc.address}).');
              break;
            }
            // The public key is not a secret — it ships on every transaction
            // this account has ever sent — but handing it out silently would
            // let a page fingerprint the wallet without consent, so it still
            // needs an approval sheet.
            final approved = await _confirmPublicKey(
              origin: _originLabel,
              address: acc.address,
            );
            if (!approved) {
              await reject(4001, 'User rejected the request.');
              break;
            }
            await resolve({
              'address': acc.address,
              'algId': acc.algId,
              'algName': _algName(acc.algId),
              'publicKey': '0x${_hexAll(acc.publicKey)}',
            });
          }
          break;
        case 'animica_switchChain':
        case 'wallet_switchEthereumChain':
          {
            // The mobile wallet is built for one chain (`--dart-define`d at
            // build time); there is no network picker to switch. Report 4902
            // ("chain not added") for anything else, which is the code dapps
            // already branch on to offer their own add-network flow.
            final requested = _parseChainIdParam(params);
            if (requested == null) {
              await reject(-32602, 'Invalid switchChain params: expected a chain id');
              break;
            }
            // Compare against the VERIFIED chain id (the one every signature
            // is bound to), not the compile-time constant — same source as
            // `animica_chainId`, so a dapp can never be told "you're on chain
            // X" and then have its tx signed for chain Y.
            final ctx = await chainContextFor(ref.read(rpcProvider));
            if (requested != ctx.chainId) {
              await reject(
                  4902,
                  'Unrecognized chain id $requested. This wallet is configured '
                  'for chain ${ctx.chainId} only.');
              break;
            }
            await resolve(null);
          }
          break;
        case 'wallet_watchAsset':
        case 'animica_watchAsset':
        case 'animica_addToken':
          {
            // Accepted but inert: the mobile wallet's token list comes from
            // the built-in registry (services/tokens.dart) plus on-chain
            // balances, so there is no per-origin watchlist to append to.
            // Resolving true keeps dapps from dead-ending on an error for a
            // purely cosmetic request; nothing is stored and nothing is shown.
            await resolve(true);
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

  /// Build, confirm, sign and broadcast a deploy (kind=1).
  ///
  /// Shared by `animica_deployContract` and by a `sendTransaction` that
  /// carries code + manifest, so both entry points show the same sheet and
  /// produce the same canonical body.
  Future<void> _deployContract({
    required Account acc,
    required Map<String, dynamic> args,
    required Future<void> Function(dynamic value) resolve,
    required Future<void> Function(int code, String message) reject,
  }) async {
    final code = parseDeployBytes(args['code']);
    if (code == null || code.isEmpty) {
      await reject(-32602, 'Deploy params missing `code`');
      return;
    }
    final manifest = parseDeployBytes(args['manifest']);
    if (manifest == null || manifest.isEmpty) {
      await reject(-32602, 'Deploy params missing `manifest`');
      return;
    }

    // A dapp may name the account it expects to sign. We can only sign with
    // the active one, so refuse rather than silently substituting a different
    // deployer (the deploy address is derived from the sender).
    final requestedFrom = args['from'];
    if (requestedFrom is String &&
        requestedFrom.trim().isNotEmpty &&
        requestedFrom.trim().toLowerCase() != acc.address.toLowerCase()) {
      await reject(
          -32000,
          'Deploy `from` ${requestedFrom.trim()} is not the active wallet '
          'account (${acc.address}).');
      return;
    }

    if (_parseUintParam(args['value']) != BigInt.zero) {
      await reject(
          -32602,
          'Deploy transactions cannot carry a value: the canonical deploy '
          'payload is {code, manifest} with no amount field. Send ANM to the '
          'contract in a separate transfer once it is live.');
      return;
    }

    final gasLimit =
        _parseIntParam(args['gasLimit'] ?? args['gas'], kDefaultDeployGasLimit);
    final maxFee = _parseIntParam(
        args['gasPrice'] ?? args['maxFeePerGas'] ?? args['fee'],
        kDefaultGasPrice);

    final approved = await _confirmDeploy(
      from: acc.address,
      codeBytes: code.length,
      manifestBytes: manifest.length,
      gasLimit: gasLimit,
      hostLabel: _originLabel,
    );
    if (!approved) {
      await reject(4001, 'User rejected the deploy.');
      return;
    }

    final rpc = ref.read(rpcProvider);
    // A deploy has to be signed over the canonical preimage, which binds the
    // node's genesis hash / network / fork id — see AnimicaChainContext.
    final ctx = await chainContextFor(rpc);
    // The dapp-supplied `nonce` is IGNORED (extension parity). A page that
    // guesses wrong strands the tx in the mempool or collides with one the
    // wallet already sent, and the deploy address is derived from the nonce,
    // so an attacker-chosen one also picks the contract address.
    final nonce = await rpc.getPendingNonce(acc.address);
    final body = buildDeployBody(
      from: acc.address,
      code: code,
      manifest: manifest,
      nonce: nonce,
      gasLimit: gasLimit,
      maxFee: maxFee,
      chainId: ctx.chainId,
    );
    final txid = await signAndBroadcast(
      rpc: rpc,
      account: acc,
      body: body,
      chainContext: ctx,
    );
    await resolve(<String, dynamic>{
      'txid': txid,
      // Deterministic in the tx's own fields, so the launcher UI can use it
      // straight away instead of polling for the receipt.
      'contractAddress': deriveDeployedContractAddress(
        from: acc.address,
        chainId: ctx.chainId,
        nonce: nonce,
        code: code,
        manifest: manifest,
      ),
    });
  }

  /// Normalize the three sign-message param shapes into one request.
  /// Mirrors `normalizeProviderSignMessageParams` in the extension so the
  /// same dapp call signs the same bytes on both wallets.
  _SignMessageRequest? _parseSignMessageParams(String method, dynamic params) {
    if (method == 'personal_sign') {
      // eth-style: [message, address] — but wallets in the wild also send
      // [address, message], so sniff which slot holds the address.
      if (params is! List || params.isEmpty) return null;
      final first = params.first;
      final second = params.length > 1 ? params[1] : null;
      if (_looksLikeAnimAddress(first) && second is String) {
        return _SignMessageRequest(second, first as String);
      }
      if (first is String) {
        return _SignMessageRequest(
            first, _looksLikeAnimAddress(second) ? second as String : null);
      }
      return null;
    }

    final payload = params is List && params.isNotEmpty ? params.first : params;
    if (payload is String) {
      return payload.isEmpty ? null : _SignMessageRequest(payload, null);
    }
    if (payload is Map) {
      final message = payload['message'];
      if (message is! String || message.isEmpty) return null;
      final from = payload['from'];
      return _SignMessageRequest(message, from is String ? from : null);
    }
    return null;
  }

  bool _looksLikeAnimAddress(dynamic v) =>
      v is String && _animAddressRe.hasMatch(v);

  /// `[address]`, `"address"` or `{address}` → the address, if present.
  String? _parseAddressParam(dynamic params) {
    final v = params is List && params.isNotEmpty ? params.first : params;
    if (v is String && v.trim().isNotEmpty) return v.trim();
    if (v is Map) {
      final a = v['address'];
      if (a is String && a.trim().isNotEmpty) return a.trim();
    }
    return null;
  }

  /// `[1]`, `["0x1"]` or `[{chainId}]` → the chain id, or null if unparseable.
  int? _parseChainIdParam(dynamic params) {
    var v = params is List && params.isNotEmpty ? params.first : params;
    if (v is Map) v = v['chainId'] ?? v['chain_id'];
    if (v is int) return v;
    if (v is String) {
      final s = v.trim();
      if (s.isEmpty) return null;
      if (s.startsWith('0x') || s.startsWith('0X')) {
        return int.tryParse(s.substring(2), radix: 16);
      }
      return int.tryParse(s);
    }
    return null;
  }

  int _parseIntParam(dynamic v, int fallback) {
    if (v is int && v > 0) return v;
    if (v is String) {
      final s = v.trim();
      final parsed = s.startsWith('0x') || s.startsWith('0X')
          ? int.tryParse(s.substring(2), radix: 16)
          : int.tryParse(s);
      if (parsed != null && parsed > 0) return parsed;
    }
    return fallback;
  }

  /// Account.algName predates ml_dsa_65 and reports 'unknown' for 0x1003, so
  /// resolve the name here the way the extension's getPublicKey does.
  String _algName(int algId) {
    switch (algId) {
      case AnimicaConfig.algIdMlDsa65:
        return 'ml_dsa_65';
      case AnimicaConfig.algIdSphincs:
        return 'sphincs_shake_128s';
      case AnimicaConfig.algIdDilithium3:
        return 'dilithium3';
      default:
        return 'unknown';
    }
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

  Future<bool> _confirmDeploy({
    required String from,
    required int codeBytes,
    required int manifestBytes,
    required int gasLimit,
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
              const Text('Deploy contract',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text('Requested by $hostLabel',
                  style: TextStyle(
                      color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              const SizedBox(height: 14),
              _kv(c, 'From', from),
              _kv(c, 'Code', '$codeBytes bytes'),
              _kv(c, 'Manifest', '$manifestBytes bytes'),
              _kv(c, 'Gas limit', '$gasLimit'),
              const SizedBox(height: 10),
              Text(
                'This publishes new code on-chain under your address and '
                'spends gas. The wallet cannot read what the code does.',
                style: TextStyle(
                    color: Theme.of(c).colorScheme.outline, fontSize: 12),
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
                      child: const Text('Deploy'),
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

  Future<bool> _confirmSignMessage({
    required String origin,
    required String address,
    required String message,
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
              const Text('Sign message',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text('Requested by $origin',
                  style: TextStyle(
                      color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              const SizedBox(height: 14),
              _kv(c, 'Signer', address),
              const SizedBox(height: 6),
              // Show the message in full — a signature the user cannot read is
              // a blind-signing oracle. Long messages scroll rather than being
              // truncated.
              Container(
                constraints: const BoxConstraints(maxHeight: 220),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Theme.of(c).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SingleChildScrollView(
                  child: SelectableText(
                    message,
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                  ),
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

  Future<bool> _confirmPublicKey({
    required String origin,
    required String address,
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
              const Text('Share public key?',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text('Requested by $origin',
                  style: TextStyle(
                      color: Theme.of(c).colorScheme.outline, fontSize: 12)),
              const SizedBox(height: 14),
              _kv(c, 'Account', address),
              const SizedBox(height: 10),
              Text(
                'Sites need your public key to verify signatures you give '
                'them. It is not a secret — it is published on every '
                'transaction you send — and it cannot move funds.',
                style: TextStyle(
                    color: Theme.of(c).colorScheme.outline, fontSize: 12),
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
                      child: const Text('Share'),
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
    final h = _hexAll(b);
    if (h.length <= 32) return h;
    return '${h.substring(0, 24)}…${h.substring(h.length - 4)}';
  }

  String _hexAll(Uint8List b) =>
      b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

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
                // Record the real origin BEFORE any script can call the
                // bridge — this is the value _handleProviderRequest gates on.
                _setPageOrigin(url);
                if (url != null) _ctrl.text = url.toString();
                // Inject as early as possible so dapps that probe for
                // `window.animica` at document_start see it.
                if (_pageOrigin != null && _isWhitelisted(_pageOrigin!)) {
                  await c.evaluateJavascript(
                      source: _buildProviderScript(acc?.address));
                }
              },
              onLoadStop: (c, url) async {
                if (url == null) return;
                _setPageOrigin(url);
                _ctrl.text = url.toString();
                if (_pageOrigin != null && _isWhitelisted(_pageOrigin!)) {
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
