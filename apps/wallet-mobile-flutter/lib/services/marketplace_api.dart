// REST client for the animica.dev App Store API (`/api/mkt/v1`).
//
// Two request classes:
//
//   Public reads   — catalog, app detail, and the public license-verify
//                    endpoint. No auth, plain http GET/POST.
//   Buyer routes   — purchase intent/submit/status, my-licenses, and the
//                    entitlement-gated download token. These need a session.
//
// Auth (challenge-session flow, mirrors the browser web-login):
//   1. GET  /auth/challenge?address=<anim1…>&purpose=store  -> { challenge }.
//   2. Sign UTF8("animica:signMessage:" + challenge) with the active account's
//      ML-DSA-65 key (services/ml_dsa_65.dart), exactly as the node's
//      lib/wallet-verify.ts re-verifies it.
//   3. POST /auth/verify { address, challenge, signature, publicKey, purpose }
//      -> the server sets an httpOnly `anm_mkt_session` cookie. We capture it
//      from Set-Cookie and cache it (bound to the signing address) in the
//      encrypted vault under `animica.wallet.store.session.v1`.
//   4. Buyer requests send `Cookie: anm_mkt_session=<value>`. A 401 triggers a
//      single silent re-challenge (the session may have expired) before failing.
//
// The API returns amounts as decimal strings and origin-relative content /
// download URLs (`/api/mkt/v1/...`); the typed models in models/store.dart do
// the tolerant parsing and [absoluteUrl] resolves URLs against this base.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../constants.dart';
import '../models/account.dart';
import '../models/store.dart';
import 'auth.dart';
import 'ml_dsa_65.dart';

/// The exact domain-separation prefix the node signs message-logins under
/// (SIGN_MESSAGE_DOMAIN in the marketplace's lib/config.ts + lib/wallet-verify.ts).
/// Hard-coded here rather than trusting the server's echoed `domain` so the
/// wallet always signs a known, single-purpose string.
const String _signMessageDomain = 'animica:signMessage:';

/// The challenge purpose that mints a store session (scopes: read, buy, use).
const String _storePurpose = 'store';

const String _kStoreSessionKey = 'animica.wallet.store.session.v1';
const String _sessionCookieName = 'anm_mkt_session';

/// A structured error carrying the server's `{error:{code,message}}` (or a
/// synthesized transport error). UI can branch on [statusCode] to render the
/// "almost ready" / "sign in" / "unavailable" states.
class MarketplaceApiException implements Exception {
  final int statusCode;
  final String code;
  final String message;

  MarketplaceApiException(this.statusCode, this.code, this.message);

  bool get isUnauthorized => statusCode == 401;
  bool get isForbidden => statusCode == 403;
  bool get isNotFound => statusCode == 404;
  bool get isConflict => statusCode == 409;
  bool get isExpired => statusCode == 410;

  /// The store side is not yet configured / armed (STORE_TREASURY_ADDRESS,
  /// dormant rails). Surfaced by the intent route as 503.
  bool get isUnavailable => statusCode == 503;

  @override
  String toString() => 'MarketplaceApiException($statusCode/$code): $message';
}

/// A cached store session cookie, bound to the address that signed for it so a
/// session is never reused across accounts.
class StoreSession {
  final String address;
  final String cookie; // the raw `anm_mkt_session` value
  final String createdAt;

  const StoreSession({
    required this.address,
    required this.cookie,
    required this.createdAt,
  });

  Map<String, dynamic> toJson() => {
        'address': address,
        'cookie': cookie,
        'createdAt': createdAt,
      };

  factory StoreSession.fromJson(Map<String, dynamic> j) => StoreSession(
        address: (j['address'] ?? '').toString(),
        cookie: (j['cookie'] ?? '').toString(),
        createdAt: (j['createdAt'] ?? '').toString(),
      );

  bool get isUsable => cookie.isNotEmpty && address.isNotEmpty;
}

/// Persists the store session cookie in secure storage, additionally AES-GCM
/// encrypted with the unlock key when the wallet has a password set — the same
/// layering [Vault] uses for account keys. Read failures are non-fatal (a lost
/// session just means re-challenge), so this never throws on load.
class StoreSessionStore {
  final FlutterSecureStorage _storage;
  final Uint8List? unlockKey;

  StoreSessionStore({FlutterSecureStorage? storage, this.unlockKey})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(
                accessibility: KeychainAccessibility.unlocked_this_device,
              ),
            );

  Future<StoreSession?> load() async {
    String? raw;
    try {
      raw = await _storage.read(key: _kStoreSessionKey);
    } catch (_) {
      return null;
    }
    if (raw == null || raw.isEmpty) return null;
    final plain = unlockKey == null ? raw : decryptFromString(unlockKey!, raw);
    if (plain == null || plain.isEmpty) return null;
    try {
      final j = jsonDecode(plain);
      if (j is! Map<String, dynamic>) return null;
      final s = StoreSession.fromJson(j);
      return s.isUsable ? s : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> save(StoreSession session) async {
    final plain = jsonEncode(session.toJson());
    final value = unlockKey == null ? plain : encryptToString(unlockKey!, plain);
    await _storage.write(key: _kStoreSessionKey, value: value);
  }

  Future<void> clear() async {
    try {
      await _storage.delete(key: _kStoreSessionKey);
    } catch (_) {}
  }
}

class MarketplaceApi {
  /// `https://animica.dev/api/mkt/v1` — both `/store/...` and `/auth/...` hang
  /// off this. No trailing slash.
  final String baseUrl;

  /// The active wallet account. Required for buyer routes (it signs the store
  /// challenge); null is fine for public catalog reads.
  final Account? account;

  final http.Client _http;
  final bool _ownsHttp;
  final StoreSessionStore _sessions;
  final Duration _timeout;

  MarketplaceApi({
    this.account,
    String? baseUrl,
    http.Client? httpClient,
    StoreSessionStore? sessionStore,
    Uint8List? unlockKey,
    FlutterSecureStorage? storage,
    Duration timeout = const Duration(seconds: 20),
  })  : baseUrl = _stripTrailingSlash(baseUrl ?? AnimicaConfig.storeApiUrl),
        _http = httpClient ?? http.Client(),
        _ownsHttp = httpClient == null,
        _sessions = sessionStore ??
            StoreSessionStore(storage: storage, unlockKey: unlockKey),
        _timeout = timeout;

  static String _stripTrailingSlash(String s) =>
      s.endsWith('/') ? s.substring(0, s.length - 1) : s;

  /// Resolve an origin-relative API URL (icon, screenshot, download) returned by
  /// the store to an absolute URL against the configured store origin. Passes
  /// through absolute URLs and returns null for null/empty input.
  static String? absoluteUrl(String? url) {
    if (url == null || url.isEmpty) return null;
    final u = url.trim();
    if (u.startsWith('http://') || u.startsWith('https://')) return u;
    final origin = Uri.parse(AnimicaConfig.storeApiUrl).origin;
    return u.startsWith('/') ? '$origin$u' : '$origin/$u';
  }

  // ── public catalog reads ──────────────────────────────────────────────────

  /// GET /store/apps — the public store catalog. [type] is `APP` |
  /// `DIGITAL_GOOD`; [sort] is `top` | `new` | `trending`.
  Future<List<StoreApp>> catalog({
    String? category,
    String? query,
    String? type,
    String sort = 'top',
    int limit = 40,
    int offset = 0,
  }) async {
    final qp = <String, String>{
      'sort': sort,
      'limit': '$limit',
      'offset': '$offset',
    };
    if (category != null && category.isNotEmpty) qp['category'] = category;
    if (query != null && query.trim().isNotEmpty) qp['q'] = query.trim();
    if (type != null && type.isNotEmpty) qp['type'] = type;
    final uri = Uri.parse('$baseUrl/store/apps').replace(queryParameters: qp);
    final j = await _getJson(uri);
    return StoreApp.listFrom(j['apps']);
  }

  /// GET /store/apps/{slug} — full public detail (screenshots, prices, latest
  /// build with permissions, reviews).
  Future<StoreAppDetail> appDetail(String slug) async {
    final uri = Uri.parse('$baseUrl/store/apps/${Uri.encodeComponent(slug)}');
    final j = await _getJson(uri);
    final app = j['app'];
    if (app is! Map<String, dynamic>) {
      throw MarketplaceApiException(200, 'bad_response', 'missing app in response');
    }
    return StoreAppDetail.fromJson(app);
  }

  /// POST /store/licenses/verify — PUBLIC license check (validity, anchor +
  /// Merkle proof, canonical record). Pass a `licenseId` or a 64-hex `leaf`.
  /// A "not found" (404 with a `valid:false` body) is returned as a result,
  /// not thrown.
  Future<LicenseVerifyResult> verifyLicense({String? licenseId, String? leaf}) async {
    final hasId = licenseId != null && licenseId.isNotEmpty;
    final hasLeaf = leaf != null && leaf.isNotEmpty;
    if (!hasId && !hasLeaf) {
      throw ArgumentError('verifyLicense requires a licenseId or a leaf hash');
    }
    final resp = await _http
        .post(
          Uri.parse('$baseUrl/store/licenses/verify'),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            if (hasId) 'licenseId': licenseId,
            if (hasLeaf) 'leaf': leaf,
          }),
        )
        .timeout(_timeout);
    // The route answers "unknown license" with 404 + { valid:false, reasons }.
    final decoded = _tryDecodeMap(resp.body);
    if (decoded != null && decoded.containsKey('valid')) {
      return LicenseVerifyResult.fromJson(decoded);
    }
    return LicenseVerifyResult.fromJson(_expectMap(resp));
  }

  // ── buyer routes (session-authenticated) ──────────────────────────────────

  /// POST /store/purchases/intent { slug, priceId? } — mints a single-use
  /// on-chain payment intent (payTo, amountNanm, ANMSTORE1 memoHex, split).
  Future<PurchaseIntent> createPurchaseIntent({
    required String slug,
    String? priceId,
  }) async {
    final j = await _authedJson('POST', '/store/purchases/intent', body: {
      'slug': slug,
      if (priceId != null && priceId.isNotEmpty) 'priceId': priceId,
    });
    return PurchaseIntent.fromJson(j);
  }

  /// POST /store/purchases/{id}/submit { txid } — records the broadcast txid for
  /// the payment watcher. Idempotent server-side.
  Future<SubmitResult> submitPurchaseTxid({
    required String purchaseId,
    required String txid,
  }) async {
    final j = await _authedJson(
      'POST',
      '/store/purchases/${Uri.encodeComponent(purchaseId)}/submit',
      body: {'txid': txid},
    );
    return SubmitResult.fromJson(j);
  }

  /// GET /store/purchases/{id} — poll a purchase through PENDING → ACTIVE, with
  /// the payment intent (incl. watcher failReason) and issued License.
  Future<PurchaseStatusResult> purchaseStatus(String purchaseId) async {
    final j = await _authedJson(
      'GET',
      '/store/purchases/${Uri.encodeComponent(purchaseId)}',
    );
    return PurchaseStatusResult.fromJson(j);
  }

  /// GET /store/licenses — the signed-in buyer's licenses with Merkle proof +
  /// anchor info.
  Future<List<License>> myLicenses() async {
    final j = await _authedJson('GET', '/store/licenses');
    return License.listFrom(j['licenses']);
  }

  /// POST /store/apps/{slug}/download-token — entitlement-gated, short-lived
  /// token + URL + the sha3/certSha256 the wallet must re-verify post-download.
  Future<DownloadToken> requestDownloadToken({
    required String slug,
    String? buildId,
    String? channel,
  }) async {
    final j = await _authedJson(
      'POST',
      '/store/apps/${Uri.encodeComponent(slug)}/download-token',
      body: {
        if (buildId != null && buildId.isNotEmpty) 'buildId': buildId,
        if (channel != null && channel.isNotEmpty) 'channel': channel,
      },
    );
    return DownloadToken.fromJson(j);
  }

  // ── web-game play (Game Lab) ───────────────────────────────────────────────

  /// GET /store/apps/{slug}/bundle — PUBLIC, price-aware web-game bundle
  /// metadata. FREE games expose the direct content [GameBundle.playUrl] for
  /// public play; PAID games expose only `hasBundle` (play goes through the
  /// license-gated play-token). No session required.
  Future<GameBundle> gameBundle(String slug) async {
    final uri =
        Uri.parse('$baseUrl/store/apps/${Uri.encodeComponent(slug)}/bundle');
    final j = await _getJson(uri);
    return GameBundle.fromJson(j);
  }

  /// POST /store/play-token/{slug} — entitlement-gated, short-lived HMAC play
  /// token + relative play URL for a web game's self-contained HTML bundle
  /// (the PLAY twin of [requestDownloadToken]). Session-authenticated; a free
  /// game mints for anyone signed in, a paid game only for the owner or an
  /// active purchase. Throws `MarketplaceApiException(403,'not_entitled')` when
  /// a purchase is required.
  Future<PlayToken> playToken(String slug) async {
    final j = await _authedJson(
      'POST',
      '/store/play-token/${Uri.encodeComponent(slug)}',
    );
    return PlayToken.fromJson(j);
  }

  /// Resolve a FREE web game's absolute play URL via [gameBundle], or null when
  /// the listing has no bundle or is not free (paid games play via [playToken]).
  Future<String?> freePlayUrl(String slug) async {
    final info = await gameBundle(slug);
    if (!info.hasBundle || !info.free) return null;
    return absoluteUrl(info.playUrl);
  }

  // ── session management ────────────────────────────────────────────────────

  /// True when a usable cached session exists for the active account.
  Future<bool> hasSession() async {
    final acc = account;
    if (acc == null) return false;
    final s = await _sessions.load();
    return s != null && s.isUsable && s.address == acc.address;
  }

  /// Force a fresh challenge-sign-verify round even if a session is cached.
  Future<void> signIn() async {
    await _ensureCookie(force: true);
  }

  /// Drop the locally cached session (e.g. on sign-out / account switch).
  Future<void> signOut() => _sessions.clear();

  // ── internals ─────────────────────────────────────────────────────────────

  Account _requireAccount() {
    final acc = account;
    if (acc == null) {
      throw MarketplaceApiException(
          401, 'no_account', 'No active wallet account to sign in with.');
    }
    return acc;
  }

  Future<String> _ensureCookie({bool force = false}) async {
    final acc = _requireAccount();
    if (!force) {
      final cached = await _sessions.load();
      if (cached != null && cached.isUsable && cached.address == acc.address) {
        return cached.cookie;
      }
    }
    final session = await _login(acc);
    return session.cookie;
  }

  Future<StoreSession> _login(Account acc) async {
    if (acc.algId != AnimicaConfig.algIdMlDsa65) {
      throw MarketplaceApiException(
        400,
        'unsupported_scheme',
        'Store sign-in requires an ML-DSA-65 wallet. This account '
            '(0x${acc.algId.toRadixString(16)}) cannot sign in.',
      );
    }
    final address = acc.address;

    // 1. challenge
    final chalUri = Uri.parse('$baseUrl/auth/challenge').replace(queryParameters: {
      'address': address,
      'purpose': _storePurpose,
    });
    final chalJson = await _getJson(chalUri);
    final challenge = (chalJson['challenge'] ?? '').toString();
    if (challenge.isEmpty) {
      throw MarketplaceApiException(200, 'bad_challenge', 'no challenge returned');
    }

    // 2. sign UTF8(domain + challenge) with ML-DSA-65 (pure mode).
    final msgBytes = Uint8List.fromList(utf8.encode(_signMessageDomain + challenge));
    final sig = await MlDsa65.sign(acc.secretKey, msgBytes);

    // 3. verify -> Set-Cookie
    final verifyResp = await _http
        .post(
          Uri.parse('$baseUrl/auth/verify'),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            'address': address,
            'challenge': challenge,
            'signature': '0x${_hex(sig)}',
            'publicKey': '0x${_hex(acc.publicKey)}',
            'purpose': _storePurpose,
          }),
        )
        .timeout(_timeout);
    _expectMap(verifyResp); // throws MarketplaceApiException on non-2xx

    final cookie = _extractSessionCookie(verifyResp.headers['set-cookie']);
    if (cookie == null || cookie.isEmpty) {
      throw MarketplaceApiException(
          verifyResp.statusCode, 'no_session', 'sign-in returned no session cookie');
    }
    final session = StoreSession(
      address: address,
      cookie: cookie,
      createdAt: DateTime.now().toUtc().toIso8601String(),
    );
    await _sessions.save(session);
    return session;
  }

  /// Run an authenticated request, transparently re-challenging once on 401.
  Future<Map<String, dynamic>> _authedJson(
    String method,
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    final encoded = body == null ? null : jsonEncode(body);

    Future<http.Response> send(String cookie) {
      final headers = <String, String>{
        'Cookie': '$_sessionCookieName=$cookie',
        if (encoded != null) 'Content-Type': 'application/json',
      };
      switch (method) {
        case 'GET':
          return _http.get(uri, headers: headers).timeout(_timeout);
        case 'POST':
          return _http.post(uri, headers: headers, body: encoded).timeout(_timeout);
        default:
          throw ArgumentError('unsupported method $method');
      }
    }

    var cookie = await _ensureCookie();
    var resp = await send(cookie);
    if (resp.statusCode == 401) {
      // Session likely expired — re-challenge once, then give up.
      cookie = await _ensureCookie(force: true);
      resp = await send(cookie);
    }
    return _expectMap(resp);
  }

  Future<Map<String, dynamic>> _getJson(Uri uri) async {
    final resp = await _http.get(uri).timeout(_timeout);
    return _expectMap(resp);
  }

  /// Decode a 2xx JSON object, or throw a [MarketplaceApiException] built from
  /// the server's `{error:{code,message}}` envelope.
  Map<String, dynamic> _expectMap(http.Response resp) {
    final decoded = _tryDecodeMap(resp.body);
    if (resp.statusCode >= 200 && resp.statusCode < 300) {
      if (decoded != null) return decoded;
      throw MarketplaceApiException(
          resp.statusCode, 'bad_response', 'expected a JSON object');
    }
    var code = 'http_${resp.statusCode}';
    var message = 'request failed (HTTP ${resp.statusCode})';
    final err = decoded == null ? null : decoded['error'];
    if (err is Map) {
      if (err['code'] != null) code = err['code'].toString();
      if (err['message'] != null) message = err['message'].toString();
    }
    throw MarketplaceApiException(resp.statusCode, code, message);
  }

  static Map<String, dynamic>? _tryDecodeMap(String body) {
    if (body.isEmpty) return null;
    try {
      final j = jsonDecode(body);
      return j is Map<String, dynamic> ? j : null;
    } catch (_) {
      return null;
    }
  }

  /// Pull the `anm_mkt_session` value out of a Set-Cookie header. The header may
  /// carry attributes (`; Path=/; HttpOnly; …`) and, on some platforms, several
  /// comma-joined cookies — we only need our named cookie's value.
  static String? _extractSessionCookie(String? setCookie) {
    if (setCookie == null || setCookie.isEmpty) return null;
    const needle = '$_sessionCookieName=';
    final idx = setCookie.indexOf(needle);
    if (idx < 0) return null;
    var rest = setCookie.substring(idx + needle.length);
    final semi = rest.indexOf(';');
    if (semi >= 0) rest = rest.substring(0, semi);
    final value = rest.trim();
    return value.isEmpty ? null : value;
  }

  static String _hex(Uint8List b) =>
      b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();

  /// Release the owned http client. No-op when an external client was injected.
  void close() {
    if (_ownsHttp) _http.close();
  }
}
