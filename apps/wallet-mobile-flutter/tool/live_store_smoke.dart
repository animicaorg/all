// Live read-only smoke test for the App Store API client's parsing layer.
//
// Fetches the real catalog + the `e2e-test-pack` acceptance listing from the
// production backend (https://animica.dev/api/mkt/v1) and drives the EXACT
// parsers `MarketplaceApi.catalog()` / `appDetail()` use
// (`StoreApp.listFrom(j['apps'])`, `StoreAppDetail.fromJson(j['app'])`) so we
// prove the typed models handle live payloads, not just fixtures.
//
// Read-only: only public GETs, no auth, no purchase, no writes.
//
//   cd apps/wallet-mobile-flutter
//   dart run tool/live_store_smoke.dart
//
// Exits non-zero on any parse/assertion failure.

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'package:animica_wallet/models/store.dart';

const _base = 'https://animica.dev/api/mkt/v1';
const _e2eSlug = 'e2e-test-pack';

int _failures = 0;

void _check(bool ok, String label) {
  stdout.writeln('${ok ? '  PASS' : '  FAIL'}  $label');
  if (!ok) _failures++;
}

Future<Map<String, dynamic>> _getJson(String url) async {
  final resp = await http.get(Uri.parse(url)).timeout(const Duration(seconds: 20));
  if (resp.statusCode < 200 || resp.statusCode >= 300) {
    throw 'GET $url -> HTTP ${resp.statusCode}: ${resp.body}';
  }
  final j = jsonDecode(resp.body);
  if (j is! Map<String, dynamic>) throw 'GET $url did not return a JSON object';
  return j;
}

Future<void> main() async {
  stdout.writeln('Live App Store parsing smoke test  ($_base)\n');

  // 1. Catalog — GET /store/apps  (mirrors MarketplaceApi.catalog()).
  stdout.writeln('[catalog] GET /store/apps');
  final catalogJson = await _getJson('$_base/store/apps?sort=top&limit=40&offset=0');
  final apps = StoreApp.listFrom(catalogJson['apps']);
  _check(apps.isNotEmpty, 'catalog parsed >= 1 app (${apps.length})');

  final e2e = apps.where((a) => a.slug == _e2eSlug).cast<StoreApp?>().firstWhere(
        (a) => a != null,
        orElse: () => null,
      );
  _check(e2e != null, 'catalog contains the `$_e2eSlug` listing');
  if (e2e != null) {
    _check(e2e.name.isNotEmpty, 'catalog row name = "${e2e.name}"');
    _check(e2e.type == 'DIGITAL_GOOD' || e2e.type == 'APP',
        'catalog row type is a known enum ("${e2e.type}")');
    _check((e2e.publisher.address ?? '').startsWith('anim1'),
        'catalog row publisher.address is bech32m ("${e2e.publisher.address}")');
    final hp = e2e.headlinePrice;
    _check(hp != null && hp.amountNanm > BigInt.zero,
        'catalog row headlinePrice parsed as BigInt nanm '
        '(${hp?.amountNanm} nanm, model ${hp?.model})');
  }

  // 2. Detail — GET /store/apps/{slug}  (mirrors MarketplaceApi.appDetail()).
  stdout.writeln('\n[detail]  GET /store/apps/$_e2eSlug');
  final detailJson = await _getJson('$_base/store/apps/$_e2eSlug');
  final app = detailJson['app'];
  _check(app is Map<String, dynamic>, 'detail response has an `app` object');
  if (app is Map<String, dynamic>) {
    final detail = StoreAppDetail.fromJson(app);
    _check(detail.slug == _e2eSlug, 'detail.slug == $_e2eSlug');
    _check(detail.name.isNotEmpty, 'detail.name = "${detail.name}"');
    _check(detail.status != null && detail.status!.isNotEmpty,
        'detail.status parsed ("${detail.status}")');
    _check((detail.description ?? '').isNotEmpty,
        'detail.description parsed (${(detail.description ?? '').length} chars)');
    _check(detail.prices.isNotEmpty,
        'detail.prices parsed (${detail.prices.length} price row(s))');
    if (detail.prices.isNotEmpty) {
      final p = detail.prices.first;
      _check(p.amountNanm >= BigInt.zero && p.model.isNotEmpty,
          'detail price row: model=${p.model} amountNanm=${p.amountNanm} '
          'isOneTime=${p.isOneTime} isFree=${p.isFree}');
    }
    // assets / reviews / latestBuild are tolerant-optional on this listing;
    // reaching here means fromJson parsed them without throwing.
    stdout.writeln('  info  detail.assets=${detail.assets.length} '
        'reviews=${detail.reviews.length} '
        'latestBuild=${detail.latestBuild == null ? 'null' : 'present'} '
        '(tolerant-optional, parsed OK)');
  }

  stdout.writeln('');
  if (_failures == 0) {
    stdout.writeln('ALL LIVE-PARSE CHECKS PASSED');
    exit(0);
  } else {
    stdout.writeln('$_failures CHECK(S) FAILED');
    exit(1);
  }
}
