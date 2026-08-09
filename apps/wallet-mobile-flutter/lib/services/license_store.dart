// Owned-license vault + license verification.
//
// Two jobs:
//
//   1. Offline cache. The buyer's licenses (from GET /store/licenses, or the
//      License handed back by a completed purchase poll) are persisted in
//      secure storage under `animica.wallet.licenses.v1`, AES-GCM encrypted with
//      the unlock key when a password is set — the same layering [Vault] uses.
//      This lets the Library screen render entitlements offline.
//
//   2. Verification. `verifyWithServer` calls the public POST /store/licenses/
//      verify endpoint. `verifyOffline` re-derives the leaf hash from the stored
//      record and walks the Merkle inclusion proof against the anchor root —
//      byte-for-byte the same construction the marketplace's lib/license.ts uses
//      (canonical record → sha3-256 hex leaf → sorted Merkle over hex-string
//      concatenation) — so a cached license can be checked without trusting the
//      server. Revocation is API-checked (append-only anchors can't express it),
//      so the authoritative call remains the server verify; the offline check is
//      an integrity/inclusion proof, not a revocation check.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:pointycastle/digests/sha3.dart';

import '../models/store.dart';
import 'auth.dart';
import 'marketplace_api.dart';

const String _kLicensesKey = 'animica.wallet.licenses.v1';
final RegExp _hex64 = RegExp(r'^[0-9a-f]{64}$');

/// sha3-256 (FIPS 202) of a UTF-8 string, lowercase hex. Matches Node's
/// `createHash('sha3-256')` used throughout lib/license.ts.
String sha3_256HexUtf8(String input) {
  final d = SHA3Digest(256);
  final bytes = Uint8List.fromList(utf8.encode(input));
  d.update(bytes, 0, bytes.length);
  final out = Uint8List(32);
  d.doFinal(out, 0);
  return out.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
}

int _epochSeconds(DateTime? d) =>
    d == null ? 0 : d.millisecondsSinceEpoch ~/ 1000;

/// The canonical license record the leaf commits to. Field order + null→''
/// coercion + epoch-seconds timestamps are a WIRE CONTRACT with the marketplace's
/// `canonicalLicenseRecord` (lib/license.ts) — Dart's `jsonEncode` produces the
/// same compact JSON (insertion-ordered map, no spaces) for these ASCII values
/// as the server's `JSON.stringify`.
String canonicalLicenseRecord(License l) {
  final rec = <String, dynamic>{
    'licenseId': l.id,
    'purchaseTxid': l.purchaseTxid ?? '',
    'buyer': l.buyerAddress ?? '',
    'listing': l.listingId ?? '',
    'buildId': l.buildId ?? '',
    'buildSha3': l.buildSha3 ?? '',
    'certSha256': l.certSha256 ?? '',
    'kind': l.kind ?? '',
    'issued': _epochSeconds(l.issuedAtDate),
    'expires': l.expiresAtDate == null ? 0 : _epochSeconds(l.expiresAtDate),
  };
  return jsonEncode(rec);
}

/// sha3-256 hex of the canonical record — the license's Merkle leaf.
String licenseLeafHash(License l) => sha3_256HexUtf8(canonicalLicenseRecord(l));

/// Verify a leaf's inclusion proof against an anchor [root]. Fail-closed:
/// malformed steps, oversized proofs, or any mismatch => false (never throws).
/// Mirrors lib/license.ts `verifyLicenseProof`: side 'right' => parent =
/// sha3(current + sibling); side 'left' => sha3(sibling + current); the hash is
/// over the concatenated 64-hex STRINGS (not raw bytes).
bool verifyLicenseProof(
  String leafHash,
  List<Map<String, dynamic>> proof,
  String root,
) {
  final leaf = leafHash.toLowerCase();
  final r = root.toLowerCase();
  if (!_hex64.hasMatch(leaf) || !_hex64.hasMatch(r)) return false;
  if (proof.length > 64) return false;
  var h = leaf;
  for (final step in proof) {
    final sibling = step['sibling'];
    final side = step['side'];
    if (sibling is! String || !_hex64.hasMatch(sibling.toLowerCase())) {
      return false;
    }
    final sib = sibling.toLowerCase();
    if (side == 'right') {
      h = sha3_256HexUtf8(h + sib);
    } else if (side == 'left') {
      h = sha3_256HexUtf8(sib + h);
    } else {
      return false;
    }
  }
  return h == r;
}

/// Result of the local (offline) integrity + inclusion check.
class LicenseLocalCheck {
  /// The recomputed leaf matches the stored `leafHash`.
  final bool leafMatches;

  /// Merkle proof verified against the anchor root; null when the license
  /// isn't anchored yet or carries no proof (anchoring is batched).
  final bool? proofOk;

  /// The license references an on-chain anchor tx.
  final bool anchored;

  final bool revoked;
  final bool expired;

  /// The leaf we recomputed from the stored record (lowercase hex).
  final String computedLeaf;

  final List<String> reasons;

  const LicenseLocalCheck({
    required this.leafMatches,
    required this.proofOk,
    required this.anchored,
    required this.revoked,
    required this.expired,
    required this.computedLeaf,
    this.reasons = const [],
  });

  /// Locally-derivable validity: integrity holds, not locally revoked/expired,
  /// and (if a proof was present) it verified. NOT authoritative for
  /// revocation — call [LicenseStore.verifyWithServer] for that.
  bool get ok =>
      leafMatches && !revoked && !expired && (proofOk ?? true);
}

/// Encrypted, offline cache of the buyer's owned licenses.
class LicenseStore {
  final FlutterSecureStorage _storage;
  final Uint8List? unlockKey;

  LicenseStore({FlutterSecureStorage? storage, this.unlockKey})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(
                accessibility: KeychainAccessibility.unlocked_this_device,
              ),
            );

  // ── persistence ───────────────────────────────────────────────────────────

  Future<List<License>> load() async {
    String? raw;
    try {
      raw = await _storage.read(key: _kLicensesKey);
    } catch (_) {
      return const [];
    }
    if (raw == null || raw.isEmpty) return const [];
    final plain = unlockKey == null ? raw : decryptFromString(unlockKey!, raw);
    if (plain == null || plain.isEmpty) return const [];
    try {
      return License.listFrom(jsonDecode(plain));
    } catch (_) {
      return const [];
    }
  }

  Future<void> save(List<License> licenses) async {
    final plain =
        jsonEncode(licenses.map((l) => l.toJson()).toList(growable: false));
    final value = unlockKey == null ? plain : encryptToString(unlockKey!, plain);
    await _storage.write(key: _kLicensesKey, value: value);
  }

  /// Insert or replace a single license (keyed by id). No-op for id-less rows.
  Future<void> upsert(License license) async {
    if (license.id.isEmpty) return;
    final cur = await load();
    final next = cur.where((l) => l.id != license.id).toList()..add(license);
    await save(next);
  }

  /// Replace the entire cache with the given set (server = source of truth).
  Future<void> replaceAll(Iterable<License> licenses) =>
      save(licenses.where((l) => l.id.isNotEmpty).toList(growable: false));

  Future<void> remove(String id) async {
    final cur = await load();
    await save(cur.where((l) => l.id != id).toList(growable: false));
  }

  Future<License?> byId(String id) async {
    if (id.isEmpty) return null;
    for (final l in await load()) {
      if (l.id == id) return l;
    }
    return null;
  }

  Future<List<License>> forListing(String listingId) async {
    if (listingId.isEmpty) return const [];
    return (await load())
        .where((l) => l.listingId == listingId)
        .toList(growable: false);
  }

  Future<void> wipe() async {
    try {
      await _storage.delete(key: _kLicensesKey);
    } catch (_) {}
  }

  // ── verification ──────────────────────────────────────────────────────────

  /// Re-derive the leaf and walk the stored Merkle proof against the anchor
  /// root — no network. Integrity + inclusion only; see [LicenseLocalCheck.ok].
  LicenseLocalCheck verifyOffline(License license) {
    final reasons = <String>[];
    final computed = licenseLeafHash(license);
    final stored = (license.leafHash ?? '').toLowerCase();
    final leafMatches = stored.isNotEmpty && computed == stored;
    if (!leafMatches) reasons.add('leaf_mismatch');

    final revoked = license.isRevoked;
    if (revoked) reasons.add('revoked');
    final expired = license.isExpired;
    if (expired) reasons.add('expired');

    bool? proofOk;
    final anchor = license.anchor;
    final root = anchor?.merkleRoot;
    final steps = license.merkleSteps;
    if (root != null && root.isNotEmpty && steps != null) {
      // Verify inclusion of the tree's actual leaf (the stored one when present).
      proofOk = verifyLicenseProof(
        stored.isNotEmpty ? stored : computed,
        steps,
        root,
      );
      if (!proofOk) reasons.add('proof_mismatch');
    }

    return LicenseLocalCheck(
      leafMatches: leafMatches,
      proofOk: proofOk,
      anchored: anchor?.isAnchored ?? false,
      revoked: revoked,
      expired: expired,
      computedLeaf: computed,
      reasons: reasons,
    );
  }

  /// Authoritative check via POST /store/licenses/verify (validity incl.
  /// revocation, anchor + proof, canonical record). Prefers the license id,
  /// falling back to the leaf hash.
  Future<LicenseVerifyResult> verifyWithServer(
    MarketplaceApi api,
    License license,
  ) {
    if (license.id.isNotEmpty) {
      return api.verifyLicense(licenseId: license.id);
    }
    final leaf = license.leafHash;
    if (leaf != null && leaf.isNotEmpty) {
      return api.verifyLicense(leaf: leaf);
    }
    throw ArgumentError('license has neither an id nor a leaf hash to verify');
  }

  /// Fetch the buyer's licenses from the server and overwrite the cache.
  Future<List<License>> refreshFromServer(MarketplaceApi api) async {
    final fresh = await api.myLicenses();
    await replaceAll(fresh);
    return fresh;
  }
}
