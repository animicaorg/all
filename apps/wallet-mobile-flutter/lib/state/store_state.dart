// Riverpod wiring for the App Store surface.
//
// The MarketplaceApi (services/marketplace_api.dart) is bound to the active
// account + the current unlock key so its buyer routes can sign the store
// challenge and its cached session is encrypted the same way the accounts vault
// is. Catalog/detail reads are public and need neither.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/store.dart';
import '../services/license_store.dart';
import '../services/marketplace_api.dart';
import 'wallet_state.dart';

/// App Store categories: (display label, AppCategory API value). `null` value =
/// "all". Order mirrors the store-design chip row.
const List<({String label, String? value})> storeCategories = [
  (label: 'All', value: null),
  (label: 'Games', value: 'GAMES'),
  (label: 'AI Agents', value: 'AI_AGENTS'),
  (label: 'Tools', value: 'TOOLS'),
  (label: 'Developer', value: 'DEV_APPS'),
  (label: 'Mobile', value: 'MOBILE'),
  (label: 'Web', value: 'WEB'),
];

/// MarketplaceApi rebuilt whenever the active account or unlock key changes.
final marketplaceApiProvider = Provider<MarketplaceApi>((ref) {
  final account = ref.watch(activeAccountProvider);
  final unlockKey = ref.watch(authProvider).value?.unlockKey;
  final api = MarketplaceApi(account: account, unlockKey: unlockKey);
  ref.onDispose(api.close);
  return api;
});

/// Encrypted offline cache of owned licenses.
final licenseStoreProvider = Provider<LicenseStore>((ref) {
  final unlockKey = ref.watch(authProvider).value?.unlockKey;
  return LicenseStore(unlockKey: unlockKey);
});

/// Selected catalog category (AppCategory value or null = all).
final storeCategoryProvider = StateProvider<String?>((_) => null);

/// Free-text catalog search (empty = none).
final storeQueryProvider = StateProvider<String>((_) => '');

/// GET /store/apps for the current category + query. Public; no session.
final storeCatalogProvider =
    FutureProvider.autoDispose<List<StoreApp>>((ref) async {
  final category = ref.watch(storeCategoryProvider);
  final query = ref.watch(storeQueryProvider);
  final api = ref.watch(marketplaceApiProvider);
  return api.catalog(
    category: category,
    query: query.isEmpty ? null : query,
    sort: 'top',
  );
});

/// GET /store/apps/{slug} full detail. Public; no session.
final storeAppDetailProvider =
    FutureProvider.autoDispose.family<StoreAppDetail, String>((ref, slug) async {
  final api = ref.watch(marketplaceApiProvider);
  return api.appDetail(slug);
});

/// GET /store/apps/{slug}/bundle — web-game bundle metadata (presence, free/paid,
/// direct play URL for free games). Public; no session. Used to decide whether a
/// listing shows a Play surface (e.g. per owned game in the library).
final gameBundleProvider =
    FutureProvider.autoDispose.family<GameBundle, String>((ref, slug) async {
  final api = ref.watch(marketplaceApiProvider);
  return api.gameBundle(slug);
});

/// The buyer's licenses (My Apps). Fetches from the server (auto sign-in via the
/// store challenge), caching offline; falls back to the cache when the fetch
/// fails but a cache exists.
final myLicensesProvider =
    FutureProvider.autoDispose<List<License>>((ref) async {
  final account = ref.watch(activeAccountProvider);
  if (account == null) return const [];
  final api = ref.watch(marketplaceApiProvider);
  final store = ref.watch(licenseStoreProvider);
  try {
    return await store.refreshFromServer(api);
  } catch (e) {
    final cached = await store.load();
    if (cached.isNotEmpty) return cached;
    rethrow;
  }
});
