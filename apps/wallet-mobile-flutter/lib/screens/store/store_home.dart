// App Store home — category chips + a catalog grid of published apps.
//
// Data: GET /api/mkt/v1/store/apps (public) via storeCatalogProvider. Tapping a
// tile pushes the detail route. Mirrors screens/nfts.dart (REST + Image.network
// grid) and the existing .when() loading/error/empty patterns.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../state/store_state.dart';

class StoreHomeScreen extends ConsumerWidget {
  const StoreHomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final catalog = ref.watch(storeCatalogProvider);
    final selected = ref.watch(storeCategoryProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Store'),
        actions: [
          IconButton(
            icon: const Icon(Icons.apps),
            tooltip: 'My Apps',
            onPressed: () => context.push('/store/library'),
          ),
        ],
      ),
      body: Column(
        children: [
          _CategoryChips(
            selected: selected,
            onSelect: (v) =>
                ref.read(storeCategoryProvider.notifier).state = v,
          ),
          const Divider(height: 1),
          Expanded(
            child: catalog.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => _CatalogError(
                message: _friendlyError(e),
                onRetry: () => ref.invalidate(storeCatalogProvider),
              ),
              data: (apps) {
                if (apps.isEmpty) return const _EmptyCatalog();
                return RefreshIndicator(
                  onRefresh: () async => ref.refresh(storeCatalogProvider.future),
                  child: GridView.builder(
                    padding: const EdgeInsets.all(12),
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 2,
                      childAspectRatio: 0.72,
                      crossAxisSpacing: 12,
                      mainAxisSpacing: 12,
                    ),
                    itemCount: apps.length,
                    itemBuilder: (c, i) => _AppTile(app: apps[i]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

String _friendlyError(Object e) {
  if (e is MarketplaceApiException) {
    if (e.isNotFound || e.isUnavailable) {
      return 'The App Store is not available yet. Check back soon.';
    }
    return e.message;
  }
  return 'Could not reach the App Store. Check your connection.';
}

class _CategoryChips extends StatelessWidget {
  final String? selected;
  final ValueChanged<String?> onSelect;
  const _CategoryChips({required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 52,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        itemCount: storeCategories.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (c, i) {
          final cat = storeCategories[i];
          final isSel = cat.value == selected;
          return ChoiceChip(
            label: Text(cat.label),
            selected: isSel,
            onSelected: (_) => onSelect(cat.value),
          );
        },
      ),
    );
  }
}

class _AppTile extends StatelessWidget {
  final StoreApp app;
  const _AppTile({required this.app});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final iconUrl = MarketplaceApi.absoluteUrl(app.iconUrl);
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => context.push('/store/app/${app.slug}'),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  _AppIcon(url: iconUrl, name: app.name),
                  if (app.verified) ...[
                    const Spacer(),
                    Icon(Icons.verified, size: 16, color: cs.primary),
                  ],
                ],
              ),
              const SizedBox(height: 10),
              Text(
                app.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
              ),
              const SizedBox(height: 2),
              Text(
                app.tagline ?? app.publisher.label,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: cs.outline, fontSize: 12, height: 1.2),
              ),
              const Spacer(),
              Row(
                children: [
                  _PriceChip(app: app),
                  const Spacer(),
                  if (app.ratingCount > 0) _RatingLabel(app: app),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AppIcon extends StatelessWidget {
  final String? url;
  final String name;
  const _AppIcon({required this.url, required this.name});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final placeholder = Container(
      width: 48,
      height: 48,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: cs.secondaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        name.isNotEmpty ? name[0].toUpperCase() : '?',
        style: TextStyle(
            color: cs.onSecondaryContainer,
            fontWeight: FontWeight.w700,
            fontSize: 20),
      ),
    );
    if (url == null) return placeholder;
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: Image.network(
        url!,
        width: 48,
        height: 48,
        fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => placeholder,
      ),
    );
  }
}

class _PriceChip extends StatelessWidget {
  final StoreApp app;
  const _PriceChip({required this.app});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final price = app.headlinePrice;
    final isFree = price == null || price.isFree;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: isFree ? cs.surfaceContainerHighest : cs.primaryContainer,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        isFree ? 'Free' : '${formatAnm(price.amountNanm)} ANM',
        style: TextStyle(
          color: isFree ? cs.onSurfaceVariant : cs.onPrimaryContainer,
          fontWeight: FontWeight.w700,
          fontSize: 12,
        ),
      ),
    );
  }
}

class _RatingLabel extends StatelessWidget {
  final StoreApp app;
  const _RatingLabel({required this.app});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.star, size: 14, color: cs.primary),
        const SizedBox(width: 2),
        Text(
          app.avgRating.toStringAsFixed(1),
          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
        ),
      ],
    );
  }
}

class _EmptyCatalog extends StatelessWidget {
  const _EmptyCatalog();
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      // ListView so RefreshIndicator (pull-to-retry) still works when empty.
      children: [
        const SizedBox(height: 80),
        Icon(Icons.storefront_outlined, size: 56, color: cs.outline),
        const SizedBox(height: 12),
        Center(
          child: Text('No apps here yet',
              style: Theme.of(context).textTheme.titleMedium),
        ),
        const SizedBox(height: 6),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(
            'Try another category, or check back soon as developers publish '
            'to the Animica App Store.',
            textAlign: TextAlign.center,
            style: TextStyle(color: cs.outline),
          ),
        ),
      ],
    );
  }
}

class _CatalogError extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _CatalogError({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.cloud_off, size: 48, color: cs.outline),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.tonalIcon(
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              onPressed: onRetry,
            ),
          ],
        ),
      ),
    );
  }
}
