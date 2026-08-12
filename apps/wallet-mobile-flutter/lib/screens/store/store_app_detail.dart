// App Store detail — screenshots, description, permissions, price + BUY.
//
// Data: GET /api/mkt/v1/store/apps/{slug} (public) via storeAppDetailProvider.
// BUY opens the checkout sheet (store_checkout_sheet.dart). Reviews are a stub
// list off the detail payload's `reviews[0..20]`.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../services/rpc.dart';
import '../../state/store_state.dart';
import '../../state/wallet_state.dart';
import 'game_play_screen.dart';
import 'store_checkout_sheet.dart';
import 'store_subscribe_sheet.dart';

class StoreAppDetailScreen extends ConsumerWidget {
  final String slug;
  const StoreAppDetailScreen({super.key, required this.slug});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(storeAppDetailProvider(slug));
    return Scaffold(
      appBar: AppBar(title: const Text('App details')),
      body: detail.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => _DetailError(
          message: _friendlyError(e),
          onRetry: () => ref.invalidate(storeAppDetailProvider(slug)),
        ),
        data: (app) => Column(
          children: [
            Expanded(child: _DetailBody(app: app)),
            _BuyBar(app: app),
          ],
        ),
      ),
    );
  }
}

/// Pinned bottom bar: the primary action for the listing.
///   • A playable web game (GAMES DIGITAL_GOOD with a bundle) shows **Play** —
///     FREE plays straight from the public bundle; PAID plays once owned (a
///     license is present), otherwise it falls back to BUY and Play unlocks
///     after the purchase completes.
///   • Otherwise it shows the headline price + BUY, enabled only for a paid
///     ONE_TIME price (non-custodial wallet payment); free / subscription-only
///     listings show an honest note (those flows are custodial / web store).
class _BuyBar extends ConsumerWidget {
  final StoreAppDetail app;
  const _BuyBar({required this.app});

  StorePrice? get _oneTime {
    for (final p in app.prices) {
      if (p.model == 'ONE_TIME' && !p.isFree) return p;
    }
    return null;
  }

  StorePrice? get _subscription {
    for (final p in app.prices) {
      if (p.isSubscription && !p.isFree) return p;
    }
    return null;
  }

  /// Owned = a non-revoked, non-expired license for this listing's slug. Reads
  /// the buyer's licenses; while loading / on error we treat it as not owned.
  bool _ownsSlug(AsyncValue<List<License>> licenses) {
    final list = licenses.asData?.value;
    if (list == null) return false;
    for (final l in list) {
      if (l.listing?.slug == app.slug && !l.isRevoked && !l.isExpired) {
        return true;
      }
    }
    return false;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cs = Theme.of(context).colorScheme;
    final account = ref.watch(activeAccountProvider);

    final isGame = app.isGame && app.hasBundle;
    final isFree = app.isFreeListing;

    String headerLabel = 'Price';
    final String valueLabel;
    final String cta;
    IconData? ctaIcon;
    VoidCallback? onTap;

    void play() => playStoreGame(
          context,
          api: ref.read(marketplaceApiProvider),
          slug: app.slug,
          name: app.name,
        );

    if (isGame && isFree) {
      // Free web game — playable by anyone, no sign-in / payment needed.
      headerLabel = 'Web game';
      valueLabel = 'Free to play';
      cta = 'Play';
      ctaIcon = Icons.play_arrow_rounded;
      onTap = play;
    } else if (isGame && _ownsSlug(ref.watch(myLicensesProvider))) {
      // Paid web game the buyer already owns — mint a play-token and play.
      headerLabel = 'Web game';
      valueLabel = 'Owned';
      cta = 'Play';
      ctaIcon = Icons.play_arrow_rounded;
      onTap = account == null ? null : play;
    } else {
      final price = _oneTime;
      final sub = _subscription;
      if (price != null) {
        valueLabel = '${formatAnm(price.amountNanm)} ANM';
        cta = 'Buy';
        onTap = account == null
            ? null
            : () => showStoreCheckoutSheet(context, app: app, price: price);
      } else if (sub != null) {
        // Subscriptions are CUSTODIAL (renewals debit the in-app balance) —
        // the consent sheet discloses that before we sign the recurring consent.
        headerLabel = 'Subscription';
        valueLabel =
            '${formatAnm(sub.amountNanm)} ANM / ${periodLabel(sub.periodDays)}';
        cta = 'Subscribe';
        ctaIcon = Icons.autorenew;
        onTap = account == null
            ? null
            : () => showStoreSubscribeSheet(context, app: app, price: sub);
      } else if (isFree) {
        valueLabel = 'Free';
        cta = 'Get';
        onTap = null; // free installs are custodial (web store) — not wired here
      } else {
        valueLabel = 'Subscription';
        cta = 'Subscribe';
        onTap = null; // subscription price unavailable
      }
    }

    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: BoxDecoration(
          color: cs.surface,
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(headerLabel,
                    style: TextStyle(color: cs.outline, fontSize: 12)),
                Text(valueLabel,
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, fontSize: 16)),
              ],
            ),
            const Spacer(),
            if (ctaIcon != null)
              FilledButton.icon(
                onPressed: onTap,
                icon: Icon(ctaIcon),
                label: Text(cta),
                style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 28, vertical: 14)),
              )
            else
              FilledButton(
                onPressed: onTap,
                style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 32, vertical: 14)),
                child: Text(cta),
              ),
          ],
        ),
      ),
    );
  }
}

String _friendlyError(Object e) {
  if (e is MarketplaceApiException) {
    if (e.isNotFound) return 'This app is no longer available.';
    if (e.isUnavailable) return 'The App Store is not available yet.';
    return e.message;
  }
  return 'Could not load this app. Check your connection.';
}

class _DetailBody extends StatelessWidget {
  final StoreAppDetail app;
  const _DetailBody({required this.app});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final shots = app.screenshots;
    return ListView(
      padding: EdgeInsets.zero,
      children: [
        _Header(app: app),
        if (shots.isNotEmpty) ...[
          const SizedBox(height: 8),
          _Screenshots(shots: shots),
        ],
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if ((app.description ?? '').trim().isNotEmpty) ...[
                Text('About',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                Text(app.description!.trim(),
                    style: const TextStyle(height: 1.4)),
                const SizedBox(height: 20),
              ],
              _InfoRow(app: app),
              const SizedBox(height: 16),
              if (app.latestBuild != null &&
                  app.latestBuild!.permissions.isNotEmpty)
                _Permissions(perms: app.latestBuild!.permissions),
              const SizedBox(height: 16),
              Text('Ratings & reviews',
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              _Reviews(app: app),
              const SizedBox(height: 24),
              Text(
                'Publisher: ${app.publisher.label}',
                style: TextStyle(color: cs.outline, fontSize: 12),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  final StoreAppDetail app;
  const _Header({required this.app});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final iconUrl = MarketplaceApi.absoluteUrl(app.icon?.url);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Icon(url: iconUrl, name: app.name),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(app.name,
                    style: const TextStyle(
                        fontSize: 20, fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        app.publisher.label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: cs.primary, fontSize: 13),
                      ),
                    ),
                    if (app.verified) ...[
                      const SizedBox(width: 4),
                      Icon(Icons.verified, size: 15, color: cs.primary),
                    ],
                  ],
                ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    if (app.ratingCount > 0) ...[
                      Icon(Icons.star, size: 15, color: cs.primary),
                      const SizedBox(width: 2),
                      Text('${app.avgRating.toStringAsFixed(1)} '
                          '(${app.ratingCount})'),
                      const SizedBox(width: 12),
                    ],
                    if (app.usersCount > 0)
                      Text('${app.usersCount} installs',
                          style: TextStyle(color: cs.outline, fontSize: 12)),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Icon extends StatelessWidget {
  final String? url;
  final String name;
  const _Icon({required this.url, required this.name});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final placeholder = Container(
      width: 64,
      height: 64,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: cs.secondaryContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Text(name.isNotEmpty ? name[0].toUpperCase() : '?',
          style: TextStyle(
              color: cs.onSecondaryContainer,
              fontWeight: FontWeight.w700,
              fontSize: 26)),
    );
    if (url == null) return placeholder;
    return ClipRRect(
      borderRadius: BorderRadius.circular(16),
      child: Image.network(url!,
          width: 64,
          height: 64,
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => placeholder),
    );
  }
}

class _Screenshots extends StatelessWidget {
  final List<StoreAsset> shots;
  const _Screenshots({required this.shots});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      height: 300,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: shots.length,
        separatorBuilder: (_, __) => const SizedBox(width: 12),
        itemBuilder: (c, i) {
          final url = MarketplaceApi.absoluteUrl(shots[i].url);
          return ClipRRect(
            borderRadius: BorderRadius.circular(14),
            child: Container(
              width: 170,
              color: cs.surfaceContainerHighest,
              child: url == null
                  ? const Center(child: Icon(Icons.image_not_supported))
                  : Image.network(
                      url,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) =>
                          const Center(child: Icon(Icons.broken_image)),
                    ),
            ),
          );
        },
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  final StoreAppDetail app;
  const _InfoRow({required this.app});
  @override
  Widget build(BuildContext context) {
    final b = app.latestBuild;
    final items = <(IconData, String, String)>[
      if (b?.versionName != null)
        (Icons.tag, 'Version', b!.versionName!),
      if (b?.sizeBytes != null)
        (Icons.sd_storage_outlined, 'Size', _fmtBytes(b!.sizeBytes!)),
      if (b?.minSdk != null)
        (Icons.android, 'Min SDK', '${b!.minSdk}'),
    ];
    if (items.isEmpty) return const SizedBox.shrink();
    return Wrap(
      spacing: 20,
      runSpacing: 12,
      children: items
          .map((it) => _InfoCell(icon: it.$1, label: it.$2, value: it.$3))
          .toList(),
    );
  }
}

class _InfoCell extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  const _InfoCell(
      {required this.icon, required this.label, required this.value});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Icon(icon, size: 15, color: cs.outline),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(color: cs.outline, fontSize: 12)),
        ]),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
      ],
    );
  }
}

class _Permissions extends StatelessWidget {
  final List<String> perms;
  const _Permissions({required this.perms});
  @override
  Widget build(BuildContext context) {
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: ExpansionTile(
        leading: const Icon(Icons.lock_outline),
        title: Text('Permissions (${perms.length})'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        children: perms
            .map((p) => Padding(
                  padding: const EdgeInsets.symmetric(vertical: 3),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('• '),
                      Expanded(
                        child: Text(p,
                            style: const TextStyle(
                                fontFamily: 'monospace', fontSize: 12)),
                      ),
                    ],
                  ),
                ))
            .toList(),
      ),
    );
  }
}

class _Reviews extends StatelessWidget {
  final StoreAppDetail app;
  const _Reviews({required this.app});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (app.reviews.isEmpty) {
      return Text('No reviews yet.', style: TextStyle(color: cs.outline));
    }
    return Column(
      children: app.reviews.take(10).map((r) {
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: List.generate(
                  5,
                  (i) => Icon(
                    i < r.rating ? Icons.star : Icons.star_border,
                    size: 15,
                    color: cs.primary,
                  ),
                ),
              ),
              if ((r.text ?? '').trim().isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(r.text!.trim()),
              ],
            ],
          ),
        );
      }).toList(),
    );
  }
}

class _DetailError extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _DetailError({required this.message, required this.onRetry});
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

String _fmtBytes(BigInt bytes) {
  final b = bytes.toDouble();
  const units = ['B', 'KB', 'MB', 'GB'];
  var v = b;
  var u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  final s = v >= 100 || u == 0 ? v.toStringAsFixed(0) : v.toStringAsFixed(1);
  return '$s ${units[u]}';
}
