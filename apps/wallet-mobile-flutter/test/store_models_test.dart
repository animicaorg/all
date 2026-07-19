// Tests for the App Store models (tolerant fromJson) + the license leaf /
// Merkle-proof helpers that back offline license verification.

import 'package:animica_wallet/models/store.dart';
import 'package:animica_wallet/services/license_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('StoreApp.fromJson tolerance', () {
    test('parses a full catalog row with string amounts', () {
      final app = StoreApp.fromJson({
        'slug': 'cool-game',
        'name': 'Cool Game',
        'tagline': 'Fun times',
        'type': 'APP',
        'category': 'GAMES',
        'verified': true,
        'usersCount': '1234',
        'avgRating': 4.5,
        'ratingCount': 88,
        'publisher': {'address': 'anim1pub', 'anm': 'acme.anm'},
        'iconUrl': '/api/mkt/v1/content/abc',
        'prices': [
          {'id': 'p1', 'model': 'ONE_TIME', 'amountNanm': '5000000000'},
          {'id': 'p0', 'model': 'FREE', 'amountNanm': '0'},
        ],
        'latestBuild': {'versionCode': 7, 'versionName': '1.0.7', 'sha3': 'deadbeef'},
      });
      expect(app.slug, 'cool-game');
      expect(app.name, 'Cool Game');
      expect(app.verified, isTrue);
      expect(app.usersCount, 1234);
      expect(app.avgRating, 4.5);
      expect(app.publisher.label, 'acme.anm');
      expect(app.prices.length, 2);
      expect(app.latestBuild?.versionCode, 7);
      // headlinePrice picks the cheapest paid price.
      expect(app.headlinePrice?.amountNanm, BigInt.from(5000000000));
    });

    test('missing / null fields never throw and get sane defaults', () {
      final app = StoreApp.fromJson({'slug': 'bare'});
      expect(app.slug, 'bare');
      expect(app.name, 'bare'); // falls back to slug
      expect(app.type, 'APP');
      expect(app.verified, isFalse);
      expect(app.usersCount, 0);
      expect(app.avgRating, 0);
      expect(app.prices, isEmpty);
      expect(app.latestBuild, isNull);
      expect(app.publisher.label, 'Unknown publisher');
      expect(app.headlinePrice, isNull);
    });

    test('listFrom drops rows without a slug and ignores non-maps', () {
      final apps = StoreApp.listFrom([
        {'slug': 'a', 'name': 'A'},
        {'name': 'no slug'},
        'garbage',
        42,
      ]);
      expect(apps.map((a) => a.slug), ['a']);
    });
  });

  group('StorePrice', () {
    test('free detection via model or zero amount', () {
      expect(StorePrice.fromJson({'model': 'FREE', 'amountNanm': '0'}).isFree, isTrue);
      expect(StorePrice.fromJson({'model': 'ONE_TIME', 'amountNanm': '0'}).isFree, isTrue);
      expect(StorePrice.fromJson({'model': 'ONE_TIME', 'amountNanm': '10'}).isFree, isFalse);
    });
  });

  group('PurchaseIntent', () {
    test('parses split + decodes memo bytes (with 0x tolerance)', () {
      final intent = PurchaseIntent.fromJson({
        'purchaseId': 'pur_1',
        'payTo': 'anim1treasury',
        'amountNanm': '5000000000',
        'memoHex': '0x414e4d53544f524531', // "ANMSTORE1"
        'expiresAt': '2026-07-19T00:00:00.000Z',
        'split': {'creatorNanm': '3500000000', 'treasuryNanm': '1500000000', 'feeBps': 3000},
      });
      expect(intent.purchaseId, 'pur_1');
      expect(intent.payTo, 'anim1treasury');
      expect(intent.amountNanm, BigInt.from(5000000000));
      expect(intent.split?.feeBps, 3000);
      expect(intent.split?.creatorNanm, BigInt.from(3500000000));
      expect(intent.memoBytes,
          [0x41, 0x4e, 0x4d, 0x53, 0x54, 0x4f, 0x52, 0x45, 0x31]);
    });

    test('junk / odd-length memo hex yields empty bytes, not a crash', () {
      expect(PurchaseIntent.fromJson({'memoHex': 'zzz'}).memoBytes, isEmpty);
      expect(PurchaseIntent.fromJson({'memoHex': 'abc'}).memoBytes, isEmpty);
      expect(PurchaseIntent.fromJson(const {}).memoBytes, isEmpty);
    });
  });

  group('PurchaseStatusResult', () {
    test('parses purchase + intent + license, tolerant of missing pieces', () {
      final res = PurchaseStatusResult.fromJson({
        'purchase': {'id': 'pur_1', 'status': 'ACTIVE', 'amountNanm': '10', 'source': 'wallet'},
        'intent': {'payTo': 'anim1t', 'amountNanm': '10', 'verifiedAt': '2026-07-19T00:00:00Z'},
        'license': {'id': 'lic_1', 'kind': 'PERPETUAL', 'leafHash': 'ab'},
      });
      expect(res.purchase.isActive, isTrue);
      expect(res.intent?.isVerified, isTrue);
      expect(res.license?.id, 'lic_1');

      final pendingOnly = PurchaseStatusResult.fromJson({
        'purchase': {'id': 'pur_2', 'status': 'PENDING_PAYMENT', 'amountNanm': '10'},
      });
      expect(pendingOnly.purchase.isPending, isTrue);
      expect(pendingOnly.intent, isNull);
      expect(pendingOnly.license, isNull);
    });
  });

  group('License.merkleSteps', () {
    test('normalizes a bare step array', () {
      final l = License.fromJson({
        'id': 'lic_1',
        'merkleProof': [
          {'sibling': 'aa', 'side': 'right'},
          {'sibling': 'bb', 'side': 'left'},
        ],
      });
      expect(l.merkleSteps?.length, 2);
      expect(l.merkleSteps?.first['side'], 'right');
    });

    test('normalizes an anchor-worker envelope {steps:[...]}', () {
      final l = License.fromJson({
        'id': 'lic_1',
        'merkleProofJson': {
          'leafHash': 'cc',
          'steps': [
            {'sibling': 'dd', 'side': 'right'},
          ],
        },
      });
      expect(l.merkleSteps?.length, 1);
      expect(l.merkleSteps?.first['sibling'], 'dd');
    });

    test('null proof => null steps', () {
      expect(License.fromJson({'id': 'x'}).merkleSteps, isNull);
    });
  });

  group('License validity flags', () {
    test('expired + revoked detection', () {
      final revoked = License.fromJson({'id': 'r', 'revokedAt': '2026-01-01T00:00:00Z'});
      expect(revoked.isRevoked, isTrue);
      expect(revoked.looksValidLocally, isFalse);

      final expired = License.fromJson({'id': 'e', 'expiresAt': '2000-01-01T00:00:00Z'});
      expect(expired.isExpired, isTrue);

      final good = License.fromJson({'id': 'g', 'expiresAt': '2999-01-01T00:00:00Z'});
      expect(good.isExpired, isFalse);
      expect(good.looksValidLocally, isTrue);
    });

    test('toJson round-trips through fromJson', () {
      final l = License.fromJson({
        'id': 'lic_1',
        'listingId': 'lst_1',
        'purchaseTxid': 'ab' * 32,
        'buyerAddress': 'anim1buyer',
        'kind': 'PERPETUAL',
        'issuedAt': '2026-01-01T00:00:00.000Z',
        'leafHash': 'cd' * 32,
        'anchor': {'seq': 3, 'merkleRoot': 'ef' * 32, 'txid': '01' * 32, 'status': 'confirmed'},
      });
      final round = License.fromJson(Map<String, dynamic>.from(
          {...l.toJson()}));
      expect(round.id, 'lic_1');
      expect(round.listingId, 'lst_1');
      expect(round.anchor?.isConfirmed, isTrue);
      expect(round.anchor?.isAnchored, isTrue);
    });
  });

  group('license leaf hashing (wire contract with lib/license.ts)', () {
    test('sha3-256 matches the FIPS-202 "abc" test vector', () {
      expect(sha3_256HexUtf8('abc'),
          '3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532');
    });

    test('canonicalLicenseRecord field order + epoch seconds + null->""', () {
      final l = License.fromJson({
        'id': 'lic1',
        'purchaseTxid': 'txid1',
        'buyerAddress': 'anim1buyer',
        'listingId': 'lst1',
        'buildId': 'bld1',
        'buildSha3': 'sha1',
        'certSha256': 'cert1',
        'kind': 'PERPETUAL',
        'issuedAt': '2026-01-01T00:00:00.000Z',
        // expiresAt absent -> "expires":0
      });
      expect(
        canonicalLicenseRecord(l),
        '{"licenseId":"lic1","purchaseTxid":"txid1","buyer":"anim1buyer",'
        '"listing":"lst1","buildId":"bld1","buildSha3":"sha1",'
        '"certSha256":"cert1","kind":"PERPETUAL","issued":1767225600,"expires":0}',
      );
      // leaf is sha3-256 of exactly that record.
      expect(licenseLeafHash(l), sha3_256HexUtf8(canonicalLicenseRecord(l)));
    });

    test('nulls coerce to empty strings in the record', () {
      final l = License.fromJson({'id': 'only-id', 'kind': 'SUBSCRIPTION'});
      expect(
        canonicalLicenseRecord(l),
        '{"licenseId":"only-id","purchaseTxid":"","buyer":"","listing":"",'
        '"buildId":"","buildSha3":"","certSha256":"","kind":"SUBSCRIPTION",'
        '"issued":0,"expires":0}',
      );
    });
  });

  group('verifyLicenseProof', () {
    test('single-leaf tree: empty proof, root == leaf', () {
      final leaf = sha3_256HexUtf8('solo');
      expect(verifyLicenseProof(leaf, const [], leaf), isTrue);
    });

    test('two-leaf tree verifies both inclusion proofs, rejects tampering', () {
      final a = sha3_256HexUtf8('leaf-a');
      final b = sha3_256HexUtf8('leaf-b');
      final sorted = [a, b]..sort();
      final root = sha3_256HexUtf8(sorted[0] + sorted[1]);

      // sorted[0] is at even index -> sibling on the right.
      expect(
        verifyLicenseProof(sorted[0], [
          {'sibling': sorted[1], 'side': 'right'},
        ], root),
        isTrue,
      );
      // sorted[1] is at odd index -> sibling on the left.
      expect(
        verifyLicenseProof(sorted[1], [
          {'sibling': sorted[0], 'side': 'left'},
        ], root),
        isTrue,
      );
      // wrong side breaks it.
      expect(
        verifyLicenseProof(sorted[0], [
          {'sibling': sorted[1], 'side': 'left'},
        ], root),
        isFalse,
      );
      // wrong root breaks it.
      expect(
        verifyLicenseProof(sorted[0], [
          {'sibling': sorted[1], 'side': 'right'},
        ], sha3_256HexUtf8('other')),
        isFalse,
      );
    });

    test('fail-closed on malformed input', () {
      final leaf = sha3_256HexUtf8('x');
      expect(verifyLicenseProof('not-hex', const [], leaf), isFalse);
      expect(verifyLicenseProof(leaf, [
        {'sibling': 'short', 'side': 'right'},
      ], leaf), isFalse);
      expect(verifyLicenseProof(leaf, [
        {'sibling': sha3_256HexUtf8('y'), 'side': 'sideways'},
      ], leaf), isFalse);
    });
  });
}
