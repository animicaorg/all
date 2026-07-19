// Typed models for the animica.dev App Store REST API
// (`/api/mkt/v1/store/*`).
//
// Every `fromJson` here is deliberately TOLERANT of nulls, missing keys and
// mixed value types. The server serializes through `jsonSafe` (lib/nanm.ts),
// which renders BigInt as a decimal STRING and Date as an ISO-8601 string, so
// nano-amounts arrive as strings and timestamps as strings — the parse helpers
// below normalize both. A field the server omits (e.g. a listing with no
// approved build, a price with no `perCallNanm`) must never throw; it becomes
// null / a sensible default so a partial catalog row still renders.
//
// Amounts are kept as BigInt (nano-ANM, 1 ANM = 1e9 nanm). Timestamps are kept
// as their raw ISO string with a lazily-parsed `DateTime?` getter so a
// malformed date from the wire can't crash a list build.

import 'dart:convert';

// ── tolerant scalar parsers ────────────────────────────────────────────────

String? _str(dynamic v) => v?.toString();

int? _int(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is double) return v.toInt();
  if (v is String) return int.tryParse(v.trim());
  return null;
}

int _intOr(dynamic v, int fallback) => _int(v) ?? fallback;

double _doubleOr(dynamic v, double fallback) {
  if (v == null) return fallback;
  if (v is num) return v.toDouble();
  if (v is String) return double.tryParse(v.trim()) ?? fallback;
  return fallback;
}

bool _boolOr(dynamic v, bool fallback) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  if (v is String) {
    final s = v.trim().toLowerCase();
    if (s == 'true' || s == '1') return true;
    if (s == 'false' || s == '0') return false;
  }
  return fallback;
}

/// Parse a nano-amount that may arrive as a decimal string (the common case
/// via jsonSafe), an int, or null → BigInt.zero.
BigInt _bigIntOrZero(dynamic v) => _bigInt(v) ?? BigInt.zero;

BigInt? _bigInt(dynamic v) {
  if (v == null) return null;
  if (v is BigInt) return v;
  if (v is int) return BigInt.from(v);
  if (v is double) return BigInt.from(v.toInt());
  if (v is String) {
    final s = v.trim();
    if (s.isEmpty) return null;
    return BigInt.tryParse(s);
  }
  return null;
}

Map<String, dynamic> _map(dynamic v) =>
    v is Map<String, dynamic> ? v : const <String, dynamic>{};

Map<String, dynamic>? _mapOrNull(dynamic v) =>
    v is Map<String, dynamic> ? v : null;

List<dynamic> _list(dynamic v) => v is List ? v : const <dynamic>[];

DateTime? _date(String? iso) => iso == null ? null : DateTime.tryParse(iso);

// ── publisher ──────────────────────────────────────────────────────────────

class StorePublisher {
  final String? address;
  final String? displayName;

  /// Verified-publisher `.anm` name, e.g. `acme.anm` (null when unlinked).
  final String? anm;

  const StorePublisher({this.address, this.displayName, this.anm});

  factory StorePublisher.fromJson(Map<String, dynamic> j) => StorePublisher(
        address: _str(j['address']),
        displayName: _str(j['displayName']),
        anm: _str(j['anm']),
      );

  /// Best-effort human label for the publisher.
  String get label => anm ?? displayName ?? address ?? 'Unknown publisher';
}

// ── price ──────────────────────────────────────────────────────────────────

class StorePrice {
  final String? id;

  /// FREE | ONE_TIME | SUBSCRIPTION | USAGE.
  final String model;
  final BigInt amountNanm;
  final BigInt? perCallNanm;
  final int? periodDays;
  final String? label;

  const StorePrice({
    this.id,
    required this.model,
    required this.amountNanm,
    this.perCallNanm,
    this.periodDays,
    this.label,
  });

  factory StorePrice.fromJson(Map<String, dynamic> j) => StorePrice(
        id: _str(j['id']),
        model: _str(j['model']) ?? 'FREE',
        amountNanm: _bigIntOrZero(j['amountNanm']),
        perCallNanm: _bigInt(j['perCallNanm']),
        periodDays: _int(j['periodDays']),
        label: _str(j['label']),
      );

  bool get isFree => model == 'FREE' || amountNanm == BigInt.zero;
  bool get isOneTime => model == 'ONE_TIME';
  bool get isSubscription => model == 'SUBSCRIPTION';

  static List<StorePrice> listFrom(dynamic v) => _list(v)
      .whereType<Map<String, dynamic>>()
      .map(StorePrice.fromJson)
      .toList(growable: false);
}

// ── build (APK release summary) ─────────────────────────────────────────────

class StoreBuild {
  final int? versionCode;
  final String? versionName;
  final String? channel;

  /// sha3-256 of the APK bytes (hex) — the wallet re-hashes the download and
  /// requires a match before install.
  final String? sha3;

  /// apksigner signer-cert SHA-256 (hex) — pinned per listing.
  final String? certSha256;
  final int? minSdk;
  final int? targetSdk;
  final BigInt? sizeBytes;
  final String? releaseNotes;
  final String? createdAt;

  /// Only present on the app-detail `latestBuild` (mapped from permissionsJson).
  final List<String> permissions;

  const StoreBuild({
    this.versionCode,
    this.versionName,
    this.channel,
    this.sha3,
    this.certSha256,
    this.minSdk,
    this.targetSdk,
    this.sizeBytes,
    this.releaseNotes,
    this.createdAt,
    this.permissions = const [],
  });

  factory StoreBuild.fromJson(Map<String, dynamic> j) => StoreBuild(
        versionCode: _int(j['versionCode']),
        versionName: _str(j['versionName']),
        channel: _str(j['channel']),
        sha3: _str(j['sha3']),
        certSha256: _str(j['certSha256']),
        minSdk: _int(j['minSdk']),
        targetSdk: _int(j['targetSdk']),
        sizeBytes: _bigInt(j['sizeBytes']),
        releaseNotes: _str(j['releaseNotes']),
        createdAt: _str(j['createdAt']),
        permissions: _list(j['permissions'] ?? j['permissionsJson'])
            .map((e) => e?.toString())
            .whereType<String>()
            .toList(growable: false),
      );

  static StoreBuild? fromJsonOrNull(dynamic v) {
    final m = _mapOrNull(v);
    return m == null ? null : StoreBuild.fromJson(m);
  }

  DateTime? get createdAtDate => _date(createdAt);
}

// ── asset (icon / screenshot / banner) ──────────────────────────────────────

class StoreAsset {
  /// ICON | SCREENSHOT | BANNER.
  final String kind;
  final String? cid;

  /// Origin-relative content URL (`/api/mkt/v1/content/<cid>`). Resolve to an
  /// absolute URL with `MarketplaceApi.absoluteUrl` before handing to
  /// `Image.network`.
  final String? url;
  final int? width;
  final int? height;
  final int sortOrder;

  const StoreAsset({
    required this.kind,
    this.cid,
    this.url,
    this.width,
    this.height,
    this.sortOrder = 0,
  });

  factory StoreAsset.fromJson(Map<String, dynamic> j) => StoreAsset(
        kind: _str(j['kind']) ?? 'SCREENSHOT',
        cid: _str(j['cid']),
        url: _str(j['url']),
        width: _int(j['width']),
        height: _int(j['height']),
        sortOrder: _intOr(j['sortOrder'], 0),
      );

  bool get isIcon => kind == 'ICON';
  bool get isScreenshot => kind == 'SCREENSHOT';
  bool get isBanner => kind == 'BANNER';

  static List<StoreAsset> listFrom(dynamic v) => _list(v)
      .whereType<Map<String, dynamic>>()
      .map(StoreAsset.fromJson)
      .toList(growable: false);
}

// ── review ──────────────────────────────────────────────────────────────────

class StoreReview {
  final int rating;
  final String? text;
  final String? createdAt;

  const StoreReview({required this.rating, this.text, this.createdAt});

  factory StoreReview.fromJson(Map<String, dynamic> j) => StoreReview(
        rating: _intOr(j['rating'], 0),
        text: _str(j['text']),
        createdAt: _str(j['createdAt']),
      );

  static List<StoreReview> listFrom(dynamic v) => _list(v)
      .whereType<Map<String, dynamic>>()
      .map(StoreReview.fromJson)
      .toList(growable: false);

  DateTime? get createdAtDate => _date(createdAt);
}

// ── catalog row (GET /store/apps → apps[]) ──────────────────────────────────

class StoreApp {
  final String slug;
  final String name;
  final String? tagline;

  /// APP | DIGITAL_GOOD.
  final String type;
  final String? category;
  final String? packageName;
  final String? coverUrl;
  final bool verified;
  final int usersCount;
  final double avgRating;
  final int ratingCount;
  final String? publishedAt;
  final StorePublisher publisher;

  /// Origin-relative icon URL (resolve via `MarketplaceApi.absoluteUrl`).
  final String? iconUrl;
  final List<StorePrice> prices;
  final StoreBuild? latestBuild;

  const StoreApp({
    required this.slug,
    required this.name,
    this.tagline,
    required this.type,
    this.category,
    this.packageName,
    this.coverUrl,
    this.verified = false,
    this.usersCount = 0,
    this.avgRating = 0,
    this.ratingCount = 0,
    this.publishedAt,
    required this.publisher,
    this.iconUrl,
    this.prices = const [],
    this.latestBuild,
  });

  factory StoreApp.fromJson(Map<String, dynamic> j) => StoreApp(
        slug: _str(j['slug']) ?? '',
        name: _str(j['name']) ?? _str(j['slug']) ?? 'Untitled',
        tagline: _str(j['tagline']),
        type: _str(j['type']) ?? 'APP',
        category: _str(j['category'] ?? j['appCategory']),
        packageName: _str(j['packageName']),
        coverUrl: _str(j['coverUrl']),
        verified: _boolOr(j['verified'], false),
        usersCount: _intOr(j['usersCount'], 0),
        avgRating: _doubleOr(j['avgRating'], 0),
        ratingCount: _intOr(j['ratingCount'], 0),
        publishedAt: _str(j['publishedAt']),
        publisher: StorePublisher.fromJson(_map(j['publisher'])),
        iconUrl: _str(j['iconUrl']),
        prices: StorePrice.listFrom(j['prices']),
        latestBuild: StoreBuild.fromJsonOrNull(j['latestBuild']),
      );

  bool get isApp => type == 'APP';
  bool get isDigitalGood => type == 'DIGITAL_GOOD';

  /// The one-time / cheapest active price to headline, else null (free apps
  /// or catalog rows the server didn't expand prices on).
  StorePrice? get headlinePrice {
    if (prices.isEmpty) return null;
    final paid = prices.where((p) => !p.isFree).toList()
      ..sort((a, b) => a.amountNanm.compareTo(b.amountNanm));
    return paid.isNotEmpty ? paid.first : prices.first;
  }

  static List<StoreApp> listFrom(dynamic v) => _list(v)
      .whereType<Map<String, dynamic>>()
      .map(StoreApp.fromJson)
      .where((a) => a.slug.isNotEmpty)
      .toList(growable: false);
}

// ── full detail (GET /store/apps/{slug} → app) ──────────────────────────────

class StoreAppDetail {
  final String slug;
  final String name;
  final String? tagline;
  final String? description;
  final String type;
  final String? category;
  final String? packageName;

  /// Game Lab web game: CID of the self-contained, sandboxed HTML play bundle
  /// (`Listing.bundleCid`); null when the listing is not a playable web game.
  /// FREE games play straight from `/api/mkt/v1/content/<bundleCid>`; PAID games
  /// must go through the license-gated play-token — clients decide from prices.
  final String? bundleCid;
  final String? coverUrl;
  final bool verified;
  final String? status;
  final int usersCount;
  final double avgRating;
  final int ratingCount;
  final String? publishedAt;
  final StorePublisher publisher;
  final List<StoreAsset> assets;
  final List<StorePrice> prices;
  final StoreBuild? latestBuild;
  final List<StoreReview> reviews;

  const StoreAppDetail({
    required this.slug,
    required this.name,
    this.tagline,
    this.description,
    required this.type,
    this.category,
    this.packageName,
    this.bundleCid,
    this.coverUrl,
    this.verified = false,
    this.status,
    this.usersCount = 0,
    this.avgRating = 0,
    this.ratingCount = 0,
    this.publishedAt,
    required this.publisher,
    this.assets = const [],
    this.prices = const [],
    this.latestBuild,
    this.reviews = const [],
  });

  factory StoreAppDetail.fromJson(Map<String, dynamic> j) => StoreAppDetail(
        slug: _str(j['slug']) ?? '',
        name: _str(j['name']) ?? _str(j['slug']) ?? 'Untitled',
        tagline: _str(j['tagline']),
        description: _str(j['description']),
        type: _str(j['type']) ?? 'APP',
        category: _str(j['category'] ?? j['appCategory']),
        packageName: _str(j['packageName']),
        bundleCid: _str(j['bundleCid']),
        coverUrl: _str(j['coverUrl']),
        verified: _boolOr(j['verified'], false),
        status: _str(j['status']),
        usersCount: _intOr(j['usersCount'], 0),
        avgRating: _doubleOr(j['avgRating'], 0),
        ratingCount: _intOr(j['ratingCount'], 0),
        publishedAt: _str(j['publishedAt']),
        publisher: StorePublisher.fromJson(_map(j['publisher'])),
        assets: StoreAsset.listFrom(j['assets']),
        prices: StorePrice.listFrom(j['prices']),
        latestBuild: StoreBuild.fromJsonOrNull(j['latestBuild']),
        reviews: StoreReview.listFrom(j['reviews']),
      );

  bool get isApp => type == 'APP';
  bool get isDigitalGood => type == 'DIGITAL_GOOD';

  /// True when this is a playable Game Lab web game (a GAMES-category
  /// DIGITAL_GOOD carrying a bundle). Drives the store detail's Play surface.
  bool get isGame => isDigitalGood && category == 'GAMES';

  /// The listing carries a self-contained web-game play bundle.
  bool get hasBundle => bundleCid != null && bundleCid!.isNotEmpty;

  /// Free to play — no active prices, or every active price is the FREE model.
  /// Matches the server's `isFreeListing` (which the play routes gate on).
  bool get isFreeListing =>
      prices.isEmpty || prices.every((p) => p.model == 'FREE');

  List<StoreAsset> get screenshots =>
      assets.where((a) => a.isScreenshot).toList(growable: false);

  StoreAsset? get icon =>
      assets.where((a) => a.isIcon).cast<StoreAsset?>().firstWhere(
            (a) => true,
            orElse: () => null,
          );
}

// ── purchase intent (POST /store/purchases/intent) ──────────────────────────

class PurchaseSplit {
  final BigInt creatorNanm;
  final BigInt treasuryNanm;
  final int feeBps;

  const PurchaseSplit({
    required this.creatorNanm,
    required this.treasuryNanm,
    required this.feeBps,
  });

  factory PurchaseSplit.fromJson(Map<String, dynamic> j) => PurchaseSplit(
        creatorNanm: _bigIntOrZero(j['creatorNanm']),
        treasuryNanm: _bigIntOrZero(j['treasuryNanm']),
        feeBps: _intOr(j['feeBps'], 0),
      );
}

class PurchaseIntent {
  final String purchaseId;

  /// Dedicated store treasury address to pay (`payTo`).
  final String payTo;
  final BigInt amountNanm;

  /// ANMSTORE1 memo bytes as hex — goes into the transfer `data` field EXACTLY.
  final String memoHex;
  final String? expiresAt;
  final PurchaseSplit? split;

  const PurchaseIntent({
    required this.purchaseId,
    required this.payTo,
    required this.amountNanm,
    required this.memoHex,
    this.expiresAt,
    this.split,
  });

  factory PurchaseIntent.fromJson(Map<String, dynamic> j) => PurchaseIntent(
        purchaseId: _str(j['purchaseId']) ?? '',
        payTo: _str(j['payTo']) ?? '',
        amountNanm: _bigIntOrZero(j['amountNanm']),
        memoHex: _str(j['memoHex']) ?? '',
        expiresAt: _str(j['expiresAt']),
        split: () {
          final m = _mapOrNull(j['split']);
          return m == null ? null : PurchaseSplit.fromJson(m);
        }(),
      );

  DateTime? get expiresAtDate => _date(expiresAt);

  /// The raw memo bytes decoded from `memoHex` for the transfer `data` field.
  /// Tolerates an optional `0x` prefix and odd length (returns empty on junk).
  List<int> get memoBytes {
    var h = memoHex;
    if (h.startsWith('0x') || h.startsWith('0X')) h = h.substring(2);
    if (h.isEmpty || h.length.isOdd) return const [];
    final out = <int>[];
    for (var i = 0; i < h.length; i += 2) {
      final b = int.tryParse(h.substring(i, i + 2), radix: 16);
      if (b == null) return const [];
      out.add(b);
    }
    return out;
  }
}

// ── submit ack (POST /store/purchases/{id}/submit) ──────────────────────────

class SubmitResult {
  final String purchaseId;
  final String status;
  final String? submittedTxid;
  final bool recorded;

  const SubmitResult({
    required this.purchaseId,
    required this.status,
    this.submittedTxid,
    this.recorded = false,
  });

  factory SubmitResult.fromJson(Map<String, dynamic> j) => SubmitResult(
        purchaseId: _str(j['purchaseId']) ?? '',
        status: _str(j['status']) ?? 'PENDING_PAYMENT',
        submittedTxid: _str(j['submittedTxid']),
        recorded: _boolOr(j['recorded'], false),
      );
}

// ── purchase record + status poll (GET /store/purchases/{id}) ────────────────

class ListingRef {
  final String? slug;
  final String? name;
  final String? type;
  const ListingRef({this.slug, this.name, this.type});

  factory ListingRef.fromJson(Map<String, dynamic> j) => ListingRef(
        slug: _str(j['slug']),
        name: _str(j['name']),
        type: _str(j['type']),
      );
}

class PurchaseRecord {
  final String id;

  /// PENDING_PAYMENT | ACTIVE | REFUNDED | EXPIRED | ...
  final String status;
  final String? priceModel;
  final BigInt amountNanm;

  /// 'wallet' (non-custodial on-chain) | 'balance' (custodial ledger).
  final String? source;
  final String? txid;
  final String? expiresAt;
  final bool autoRenew;
  final String? graceUntil;
  final String? createdAt;
  final ListingRef? listing;

  const PurchaseRecord({
    required this.id,
    required this.status,
    this.priceModel,
    required this.amountNanm,
    this.source,
    this.txid,
    this.expiresAt,
    this.autoRenew = false,
    this.graceUntil,
    this.createdAt,
    this.listing,
  });

  factory PurchaseRecord.fromJson(Map<String, dynamic> j) => PurchaseRecord(
        id: _str(j['id']) ?? '',
        status: _str(j['status']) ?? 'PENDING_PAYMENT',
        priceModel: _str(j['priceModel']),
        amountNanm: _bigIntOrZero(j['amountNanm']),
        source: _str(j['source']),
        txid: _str(j['txid']),
        expiresAt: _str(j['expiresAt']),
        autoRenew: _boolOr(j['autoRenew'], false),
        graceUntil: _str(j['graceUntil']),
        createdAt: _str(j['createdAt']),
        listing: () {
          final m = _mapOrNull(j['listing']);
          return m == null ? null : ListingRef.fromJson(m);
        }(),
      );

  bool get isActive => status == 'ACTIVE';
  bool get isPending => status == 'PENDING_PAYMENT';
  DateTime? get expiresAtDate => _date(expiresAt);
}

class PaymentIntentStatus {
  final String? payTo;
  final BigInt amountNanm;
  final String? memoHex;
  final String? expiresAt;
  final String? submittedTxid;
  final String? verifiedAt;

  /// Set by the payment watcher when it rejected the submitted tx (e.g. wrong
  /// memo / insufficient value / not final). Surface it to the user verbatim.
  final String? failReason;
  final bool expired;

  const PaymentIntentStatus({
    this.payTo,
    required this.amountNanm,
    this.memoHex,
    this.expiresAt,
    this.submittedTxid,
    this.verifiedAt,
    this.failReason,
    this.expired = false,
  });

  factory PaymentIntentStatus.fromJson(Map<String, dynamic> j) =>
      PaymentIntentStatus(
        payTo: _str(j['payTo']),
        amountNanm: _bigIntOrZero(j['amountNanm']),
        memoHex: _str(j['memoHex']),
        expiresAt: _str(j['expiresAt']),
        submittedTxid: _str(j['submittedTxid']),
        verifiedAt: _str(j['verifiedAt']),
        failReason: _str(j['failReason']),
        expired: _boolOr(j['expired'], false),
      );

  bool get isVerified => verifiedAt != null;
  DateTime? get expiresAtDate => _date(expiresAt);
}

/// Combined shape returned by GET /store/purchases/{id}: the purchase plus its
/// optional payment intent and (once ACTIVE) the issued license.
class PurchaseStatusResult {
  final PurchaseRecord purchase;
  final PaymentIntentStatus? intent;
  final License? license;

  const PurchaseStatusResult({
    required this.purchase,
    this.intent,
    this.license,
  });

  factory PurchaseStatusResult.fromJson(Map<String, dynamic> j) =>
      PurchaseStatusResult(
        purchase: PurchaseRecord.fromJson(_map(j['purchase'])),
        intent: () {
          final m = _mapOrNull(j['intent']);
          return m == null ? null : PaymentIntentStatus.fromJson(m);
        }(),
        license: License.fromJsonOrNull(j['license']),
      );
}

// ── license + anchor ────────────────────────────────────────────────────────

class LicenseAnchor {
  final int? seq;
  final String? merkleRoot;
  final int? leafCount;
  final String? prevTxid;

  /// On-chain ANMLIC1 anchor txid (null until the anchor worker posts it).
  final String? txid;
  final int? blockHeight;

  /// pending | published | confirmed.
  final String? status;

  const LicenseAnchor({
    this.seq,
    this.merkleRoot,
    this.leafCount,
    this.prevTxid,
    this.txid,
    this.blockHeight,
    this.status,
  });

  factory LicenseAnchor.fromJson(Map<String, dynamic> j) => LicenseAnchor(
        seq: _int(j['seq']),
        merkleRoot: _str(j['merkleRoot']),
        leafCount: _int(j['leafCount']),
        prevTxid: _str(j['prevTxid']),
        txid: _str(j['txid']),
        blockHeight: _int(j['blockHeight']),
        status: _str(j['status']),
      );

  static LicenseAnchor? fromJsonOrNull(dynamic v) {
    final m = _mapOrNull(v);
    return m == null ? null : LicenseAnchor.fromJson(m);
  }

  bool get isConfirmed => status == 'confirmed';
  bool get isAnchored => txid != null && txid!.isNotEmpty;
}

class License {
  final String id;
  final String? listingId;
  final ListingRef? listing;
  final String? purchaseId;
  final String? purchaseTxid;
  final String? purchaseStatus;
  final String? buyerAddress;

  /// PERPETUAL | SUBSCRIPTION.
  final String? kind;
  final String? issuedAt;
  final String? expiresAt;
  final String? revokedAt;
  final String? buildId;
  final String? buildSha3;
  final String? certSha256;
  final StoreBuild? build;
  final String? leafHash;

  /// The Merkle inclusion proof exactly as stored server-side. May be a bare
  /// step array (`[{sibling, side}, ...]`) or the anchor worker's envelope
  /// (`{leafHash, root, seq, steps}`). Kept as the raw decoded JSON so
  /// [LicenseStore] can persist and re-verify it offline.
  final dynamic merkleProof;
  final LicenseAnchor? anchor;

  const License({
    required this.id,
    this.listingId,
    this.listing,
    this.purchaseId,
    this.purchaseTxid,
    this.purchaseStatus,
    this.buyerAddress,
    this.kind,
    this.issuedAt,
    this.expiresAt,
    this.revokedAt,
    this.buildId,
    this.buildSha3,
    this.certSha256,
    this.build,
    this.leafHash,
    this.merkleProof,
    this.anchor,
  });

  factory License.fromJson(Map<String, dynamic> j) => License(
        id: _str(j['id']) ?? '',
        listingId: _str(j['listingId']),
        listing: () {
          final m = _mapOrNull(j['listing']);
          return m == null ? null : ListingRef.fromJson(m);
        }(),
        purchaseId: _str(j['purchaseId']),
        purchaseTxid: _str(j['purchaseTxid']),
        purchaseStatus: _str(j['purchaseStatus']),
        buyerAddress: _str(j['buyerAddress']),
        kind: _str(j['kind']),
        issuedAt: _str(j['issuedAt']),
        expiresAt: _str(j['expiresAt']),
        revokedAt: _str(j['revokedAt']),
        buildId: _str(j['buildId']),
        buildSha3: _str(j['buildSha3']),
        certSha256: _str(j['certSha256']),
        build: StoreBuild.fromJsonOrNull(j['build']),
        leafHash: _str(j['leafHash']),
        // merkleProof may be a List<step> or a Map envelope — keep it verbatim.
        merkleProof: j['merkleProof'] ?? j['merkleProofJson'],
        anchor: LicenseAnchor.fromJsonOrNull(j['anchor']),
      );

  static License? fromJsonOrNull(dynamic v) {
    final m = _mapOrNull(v);
    return m == null ? null : License.fromJson(m);
  }

  static List<License> listFrom(dynamic v) => _list(v)
      .whereType<Map<String, dynamic>>()
      .map(License.fromJson)
      .where((l) => l.id.isNotEmpty)
      .toList(growable: false);

  bool get isRevoked => revokedAt != null;
  bool get isSubscription => kind == 'SUBSCRIPTION';
  DateTime? get issuedAtDate => _date(issuedAt);
  DateTime? get expiresAtDate => _date(expiresAt);

  /// True when the license has a hard expiry that is in the past.
  bool get isExpired {
    final e = expiresAtDate;
    return e != null && e.isBefore(DateTime.now());
  }

  /// Best-effort local validity used before/alongside the server check —
  /// NOT authoritative (revocation is server-side / API-checked).
  bool get looksValidLocally => !isRevoked && !isExpired;

  /// Normalize [merkleProof] (bare array or `{steps: [...]}` envelope) into a
  /// plain list of `{sibling, side}` step maps for offline verification, or
  /// null when there is no usable proof.
  List<Map<String, dynamic>>? get merkleSteps {
    final p = merkleProof;
    List<dynamic>? raw;
    if (p is List) {
      raw = p;
    } else if (p is Map && p['steps'] is List) {
      raw = p['steps'] as List;
    }
    if (raw == null) return null;
    return raw.whereType<Map<String, dynamic>>().toList(growable: false);
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        if (listingId != null) 'listingId': listingId,
        if (listing != null)
          'listing': {
            if (listing!.slug != null) 'slug': listing!.slug,
            if (listing!.name != null) 'name': listing!.name,
            if (listing!.type != null) 'type': listing!.type,
          },
        if (purchaseId != null) 'purchaseId': purchaseId,
        if (purchaseTxid != null) 'purchaseTxid': purchaseTxid,
        if (purchaseStatus != null) 'purchaseStatus': purchaseStatus,
        if (buyerAddress != null) 'buyerAddress': buyerAddress,
        if (kind != null) 'kind': kind,
        if (issuedAt != null) 'issuedAt': issuedAt,
        if (expiresAt != null) 'expiresAt': expiresAt,
        if (revokedAt != null) 'revokedAt': revokedAt,
        if (buildId != null) 'buildId': buildId,
        if (buildSha3 != null) 'buildSha3': buildSha3,
        if (certSha256 != null) 'certSha256': certSha256,
        if (leafHash != null) 'leafHash': leafHash,
        if (merkleProof != null) 'merkleProof': merkleProof,
        if (anchor != null)
          'anchor': {
            if (anchor!.seq != null) 'seq': anchor!.seq,
            if (anchor!.merkleRoot != null) 'merkleRoot': anchor!.merkleRoot,
            if (anchor!.leafCount != null) 'leafCount': anchor!.leafCount,
            if (anchor!.prevTxid != null) 'prevTxid': anchor!.prevTxid,
            if (anchor!.txid != null) 'txid': anchor!.txid,
            if (anchor!.blockHeight != null) 'blockHeight': anchor!.blockHeight,
            if (anchor!.status != null) 'status': anchor!.status,
          },
      };

  String encode() => jsonEncode(toJson());
}

// ── license verify result (POST /store/licenses/verify) ─────────────────────

class LicenseVerifyResult {
  final bool valid;
  final List<String> reasons;
  final bool anchored;
  final String? leafHash;
  final String? purchaseTxid;

  /// The exact canonical record string the server hashed to the leaf; a third
  /// party can recompute `sha3-256(canonicalRecord)` and match `leafHash`.
  final String? canonicalRecord;
  final dynamic merkleProof;

  /// Server-side proof-against-anchor-root result: true/false, or null when the
  /// license is not anchored yet (anchoring is batched).
  final bool? proofOk;
  final LicenseAnchor? anchor;

  const LicenseVerifyResult({
    required this.valid,
    this.reasons = const [],
    this.anchored = false,
    this.leafHash,
    this.purchaseTxid,
    this.canonicalRecord,
    this.merkleProof,
    this.proofOk,
    this.anchor,
  });

  factory LicenseVerifyResult.fromJson(Map<String, dynamic> j) =>
      LicenseVerifyResult(
        valid: _boolOr(j['valid'], false),
        reasons: _list(j['reasons'])
            .map((e) => e?.toString())
            .whereType<String>()
            .toList(growable: false),
        anchored: _boolOr(j['anchored'], false),
        leafHash: _str(_mapOrNull(j['license'])?['leafHash'] ?? j['leafHash']),
        purchaseTxid: _str(j['purchaseTxid']),
        canonicalRecord: _str(j['canonicalRecord']),
        merkleProof: j['merkleProof'],
        proofOk: j['proofOk'] is bool ? j['proofOk'] as bool : null,
        anchor: LicenseAnchor.fromJsonOrNull(j['anchor']),
      );
}

// ── download token (POST /store/apps/{slug}/download-token) ──────────────────

class DownloadToken {
  final String token;

  /// Origin-relative download URL (`/api/mkt/v1/store/download/<token>`).
  final String? url;
  final String? expiresAt;
  final String? buildId;
  final int? versionCode;
  final String? versionName;
  final String? channel;
  final String? packageName;
  final BigInt? sizeBytes;

  /// sha3-256 of the APK bytes (hex) the wallet MUST match post-download.
  final String? sha3;
  final String? certSha256;
  final int? minSdk;

  const DownloadToken({
    required this.token,
    this.url,
    this.expiresAt,
    this.buildId,
    this.versionCode,
    this.versionName,
    this.channel,
    this.packageName,
    this.sizeBytes,
    this.sha3,
    this.certSha256,
    this.minSdk,
  });

  factory DownloadToken.fromJson(Map<String, dynamic> j) => DownloadToken(
        token: _str(j['token']) ?? '',
        url: _str(j['url']),
        expiresAt: _str(j['expiresAt']),
        buildId: _str(j['buildId']),
        versionCode: _int(j['versionCode']),
        versionName: _str(j['versionName']),
        channel: _str(j['channel']),
        packageName: _str(j['packageName']),
        sizeBytes: _bigInt(j['sizeBytes']),
        sha3: _str(j['sha3']),
        certSha256: _str(j['certSha256']),
        minSdk: _int(j['minSdk']),
      );

  DateTime? get expiresAtDate => _date(expiresAt);
}

// ── web-game bundle metadata (GET /store/apps/{slug}/bundle) ─────────────────

/// Price-aware metadata for a Game Lab web-game play bundle. The route is public:
/// FREE listings expose the direct content [playUrl] (public, immutable play);
/// PAID listings expose only [hasBundle] — their bytes are served license-gated
/// through the play-token, so the public content URL never leaks a paywall.
class GameBundle {
  final String slug;
  final bool hasBundle;
  final bool free;
  final String? mime;
  final String? cid;

  /// Origin-relative content play URL (`/api/mkt/v1/content/<cid>`) for FREE
  /// games (or the owner); null for a paid game to a non-owner. Resolve to an
  /// absolute URL with `MarketplaceApi.absoluteUrl` before loading in a WebView.
  final String? playUrl;
  final BigInt? size;

  const GameBundle({
    required this.slug,
    this.hasBundle = false,
    this.free = false,
    this.mime,
    this.cid,
    this.playUrl,
    this.size,
  });

  factory GameBundle.fromJson(Map<String, dynamic> j) => GameBundle(
        slug: _str(j['slug']) ?? '',
        hasBundle: _boolOr(j['hasBundle'], false),
        free: _boolOr(j['free'], false),
        mime: _str(j['mime']),
        cid: _str(j['cid']),
        playUrl: _str(j['playUrl']),
        size: _bigInt(j['size']),
      );

  /// A FREE game whose public content URL is directly playable (no play-token).
  bool get isFreePlayable =>
      hasBundle && free && playUrl != null && playUrl!.isNotEmpty;
}

// ── play token (POST /store/play-token/{slug}) ───────────────────────────────

/// Entitlement-gated, short-lived (~10 min) HMAC token + relative play URL for a
/// web game's self-contained bundle. Minted only for the owner or an active
/// purchase; the PLAY twin of [DownloadToken]. Entitlement is re-checked when the
/// bundle is served, so a refund/delist between mint and load still blocks.
class PlayToken {
  final String token;

  /// Origin-relative play URL (`/api/mkt/v1/store/play/<token>`). Resolve with
  /// `MarketplaceApi.absoluteUrl` before loading in a WebView.
  final String url;
  final String? expiresAt;
  final String? slug;
  final String? listingId;
  final String? priceModel;

  const PlayToken({
    required this.token,
    required this.url,
    this.expiresAt,
    this.slug,
    this.listingId,
    this.priceModel,
  });

  factory PlayToken.fromJson(Map<String, dynamic> j) => PlayToken(
        token: _str(j['token']) ?? '',
        url: _str(j['url']) ?? '',
        expiresAt: _str(j['expiresAt']),
        slug: _str(j['slug']),
        listingId: _str(j['listingId']),
        priceModel: _str(j['priceModel']),
      );

  DateTime? get expiresAtDate => _date(expiresAt);
}
