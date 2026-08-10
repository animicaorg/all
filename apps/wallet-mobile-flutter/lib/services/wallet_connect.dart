// Pair the wallet with a website over the animica.xyz relay.
//
// A desktop browser cannot reach the phone, so the site brokers the link:
// it mints a session, renders `animica://wc?...` as a QR code, and waits.
// We scan (or deep-link) that URI, approve it, and from then on poll the
// relay for sign / send requests the site queues for us.
//
// The `key` carried in the URI is the wallet-side secret: it authorises this
// device — and only this device — to approve the session and answer its
// requests. The site holds a separate secret it cannot swap for ours.
//
// This is deliberately poll-based rather than socket-based: sessions are
// short-lived, the payloads are tiny, and polling survives the app being
// backgrounded and resumed without any reconnect logic.

library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// Version of the pairing URI this build understands.
const int kWalletConnectVersion = 1;

/// How often to ask the relay whether the site queued anything for us.
const Duration kWalletConnectPollInterval = Duration(seconds: 2);

class WalletConnectUri {
  final String sessionId;
  final String walletSecret;
  final String origin;
  final String apiBase;
  final int chainId;

  const WalletConnectUri({
    required this.sessionId,
    required this.walletSecret,
    required this.origin,
    required this.apiBase,
    required this.chainId,
  });

  /// Parse `animica://wc?v=1&sid=..&key=..&origin=..&api=..&chainId=..`.
  ///
  /// Returns null for anything that is not a pairing URI so callers can feed
  /// arbitrary scanned text in without pre-filtering. Throws
  /// [WalletConnectError] when it *is* a pairing URI but an unusable one —
  /// that distinction matters for the scanner UI, which should ignore random
  /// QR codes silently but explain a malformed Animica one.
  static WalletConnectUri? tryParse(String raw) {
    final text = raw.trim();
    Uri uri;
    try {
      uri = Uri.parse(text);
    } catch (_) {
      return null;
    }
    final isScheme = uri.scheme == 'animica' && (uri.host == 'wc' || uri.path == 'wc');
    if (!isScheme) return null;

    final q = uri.queryParameters;
    final version = int.tryParse(q['v'] ?? '') ?? 0;
    if (version != kWalletConnectVersion) {
      throw WalletConnectError(
        'This pairing code needs a newer Animica wallet. Update the app and try again.',
      );
    }

    final sessionId = q['sid'] ?? '';
    final walletSecret = q['key'] ?? '';
    final api = q['api'] ?? '';
    if (sessionId.isEmpty || walletSecret.isEmpty || api.isEmpty) {
      throw WalletConnectError('Pairing code is incomplete.');
    }

    final apiUri = Uri.tryParse(api);
    if (apiUri == null || !apiUri.isScheme('https')) {
      // http:// would let a hostile QR downgrade the channel; the relay is
      // always TLS in practice.
      throw WalletConnectError('Pairing code points at an insecure server.');
    }

    return WalletConnectUri(
      sessionId: sessionId,
      walletSecret: walletSecret,
      origin: q['origin'] ?? apiUri.origin,
      apiBase: api.replaceAll(RegExp(r'/$'), ''),
      chainId: int.tryParse(q['chainId'] ?? '') ?? 1,
    );
  }
}

class WalletConnectError implements Exception {
  final String message;
  WalletConnectError(this.message);
  @override
  String toString() => message;
}

/// A request the site is waiting on us to answer.
class WalletConnectRequest {
  final String requestId;

  /// `signMessage` or `sendTransaction`.
  final String kind;
  final Map<String, dynamic> payload;
  final String createdAt;

  const WalletConnectRequest({
    required this.requestId,
    required this.kind,
    required this.payload,
    required this.createdAt,
  });

  static WalletConnectRequest fromJson(Map<String, dynamic> j) => WalletConnectRequest(
        requestId: j['requestId'] as String,
        kind: j['kind'] as String? ?? '',
        payload: Map<String, dynamic>.from(j['payload'] as Map? ?? const {}),
        createdAt: j['createdAt'] as String? ?? '',
      );
}

/// An approved pairing, persisted so it survives an app restart.
class WalletConnectSession {
  final String sessionId;
  final String walletSecret;
  final String origin;
  final String apiBase;
  final int chainId;

  /// Address this session was approved with — a later account switch must not
  /// silently start signing with a different key.
  final String address;
  final String connectedAt;

  const WalletConnectSession({
    required this.sessionId,
    required this.walletSecret,
    required this.origin,
    required this.apiBase,
    required this.chainId,
    required this.address,
    required this.connectedAt,
  });

  Map<String, dynamic> toJson() => {
        'sessionId': sessionId,
        'walletSecret': walletSecret,
        'origin': origin,
        'apiBase': apiBase,
        'chainId': chainId,
        'address': address,
        'connectedAt': connectedAt,
      };

  static WalletConnectSession fromJson(Map<String, dynamic> j) => WalletConnectSession(
        sessionId: j['sessionId'] as String,
        walletSecret: j['walletSecret'] as String,
        origin: j['origin'] as String? ?? '',
        apiBase: j['apiBase'] as String? ?? '',
        chainId: (j['chainId'] as num?)?.toInt() ?? 1,
        address: j['address'] as String? ?? '',
        connectedAt: j['connectedAt'] as String? ?? '',
      );
}

/// HTTP client for the relay endpoints under `/api/wallet/mobile/*`.
class WalletConnectApi {
  final http.Client _http;
  WalletConnectApi({http.Client? client}) : _http = client ?? http.Client();

  static const Duration _timeout = Duration(seconds: 15);

  Uri _url(String apiBase, String path, [Map<String, String>? query]) =>
      Uri.parse('$apiBase$path').replace(queryParameters: query);

  Map<String, dynamic> _decode(http.Response res) {
    final Map<String, dynamic> body;
    try {
      body = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {
      throw WalletConnectError('Server returned an unreadable response (${res.statusCode}).');
    }
    if (res.statusCode >= 400) {
      throw WalletConnectError(body['error'] as String? ?? 'Request failed (${res.statusCode}).');
    }
    return body;
  }

  /// Look up what a scanned pairing code is asking for, before approving it.
  Future<Map<String, dynamic>> describe(WalletConnectUri pairing) async {
    final res = await _http.get(
      _url(pairing.apiBase, '/api/wallet/mobile/pair', {
        'sessionId': pairing.sessionId,
        'walletSecret': pairing.walletSecret,
      }),
    ).timeout(_timeout);
    return _decode(res);
  }

  Future<void> approve(
    WalletConnectUri pairing, {
    required String address,
    required String publicKeyHex,
    required int algId,
    required String algName,
  }) async {
    final res = await _http
        .post(
          _url(pairing.apiBase, '/api/wallet/mobile/pair'),
          headers: const {'content-type': 'application/json'},
          body: jsonEncode({
            'action': 'approve',
            'sessionId': pairing.sessionId,
            'walletSecret': pairing.walletSecret,
            'address': address,
            'publicKey': publicKeyHex,
            'algId': algId,
            'algName': algName,
          }),
        )
        .timeout(_timeout);
    _decode(res);
  }

  Future<void> reject(WalletConnectUri pairing) async {
    final res = await _http
        .post(
          _url(pairing.apiBase, '/api/wallet/mobile/pair'),
          headers: const {'content-type': 'application/json'},
          body: jsonEncode({
            'action': 'reject',
            'sessionId': pairing.sessionId,
            'walletSecret': pairing.walletSecret,
          }),
        )
        .timeout(_timeout);
    _decode(res);
  }

  Future<List<WalletConnectRequest>> pending(WalletConnectSession session) async {
    final res = await _http.get(
      _url(session.apiBase, '/api/wallet/mobile/requests', {
        'sessionId': session.sessionId,
        'walletSecret': session.walletSecret,
      }),
    ).timeout(_timeout);
    final body = _decode(res);
    final list = body['requests'] as List? ?? const [];
    return list
        .whereType<Map>()
        .map((e) => WalletConnectRequest.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  Future<void> resolve(
    WalletConnectSession session,
    String requestId, {
    Object? result,
    int? errorCode,
    String? errorMessage,
  }) async {
    final payload = <String, dynamic>{'walletSecret': session.walletSecret};
    if (errorCode != null) {
      payload['error'] = {'code': errorCode, 'message': errorMessage ?? 'Rejected'};
    } else {
      payload['result'] = result;
    }
    final res = await _http
        .post(
          _url(session.apiBase, '/api/wallet/mobile/requests/$requestId'),
          headers: const {'content-type': 'application/json'},
          body: jsonEncode(payload),
        )
        .timeout(_timeout);
    _decode(res);
  }
}

/// Persistence for the single active pairing.
///
/// One session at a time keeps the approval model obvious: the connection
/// indicator names exactly one site, and disconnecting cannot leave a
/// forgotten second site able to queue requests.
class WalletConnectStore {
  static const String _key = 'animica.walletconnect.session';

  Future<WalletConnectSession?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null || raw.isEmpty) return null;
    try {
      return WalletConnectSession.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      await prefs.remove(_key);
      return null;
    }
  }

  Future<void> save(WalletConnectSession session) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(session.toJson()));
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }
}
