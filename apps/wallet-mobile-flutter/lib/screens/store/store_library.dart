// My Apps — the buyer's purchased licenses with status chips.
//
// Data: GET /api/mkt/v1/store/licenses via myLicensesProvider (auto sign-in with
// the store challenge, cached offline by LicenseStore). Each row shows the app,
// a status chip (Active / Pending / Expired / Revoked) and its on-chain anchor
// state. A license only exists once the payment watcher activates the purchase,
// so a just-paid item may take a few minutes to appear here.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/store.dart';
import '../../services/marketplace_api.dart';
import '../../state/store_state.dart';
import '../../state/wallet_state.dart';

class StoreLibraryScreen extends ConsumerWidget {
  const StoreLibraryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(activeAccountProvider);
    final licenses = ref.watch(myLicensesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('My Apps')),
      body: account == null
          ? const Center(child: Text('No active account.'))
          : licenses.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => _LibraryError(
                message: _friendlyError(e),
                onRetry: () => ref.invalidate(myLicensesProvider),
              ),
              data: (items) {
                if (items.isEmpty) return _EmptyLibrary();
                return RefreshIndicator(
                  onRefresh: () async => ref.refresh(myLicensesProvider.future),
                  child: ListView.separated(
                    padding: const EdgeInsets.all(12),
                    itemCount: items.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 8),
                    itemBuilder: (c, i) => _LicenseTile(license: items[i]),
                  ),
                );
              },
            ),
    );
  }
}

String _friendlyError(Object e) {
  if (e is MarketplaceApiException) {
    if (e.code == 'unsupported_scheme') {
      return 'My Apps needs an ML-DSA-65 wallet to sign in to the store.';
    }
    if (e.isNotFound || e.isUnavailable) {
      return 'The App Store is not available yet.';
    }
    return e.message;
  }
  return 'Could not load your apps. Check your connection.';
}

class _LicenseTile extends StatelessWidget {
  final License license;
  const _LicenseTile({required this.license});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final name = license.listing?.name ??
        license.listing?.slug ??
        license.listingId ??
        'App';
    final version = license.build?.versionName;
    final slug = license.listing?.slug;
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: slug == null ? null : () => context.push('/store/app/$slug'),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              CircleAvatar(
                backgroundColor: cs.secondaryContainer,
                child: Text(
                  name.isNotEmpty ? name[0].toUpperCase() : '?',
                  style: TextStyle(
                      color: cs.onSecondaryContainer,
                      fontWeight: FontWeight.w700),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 2),
                    Row(
                      children: [
                        if (version != null) ...[
                          Text('v$version',
                              style: TextStyle(color: cs.outline, fontSize: 12)),
                          const SizedBox(width: 8),
                        ],
                        _AnchorBadge(license: license),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              _StatusChip(license: license),
            ],
          ),
        ),
      ),
    );
  }
}

enum _Status { active, pending, expired, revoked }

class _StatusChip extends StatelessWidget {
  final License license;
  const _StatusChip({required this.license});

  _Status get _status {
    if (license.isRevoked) return _Status.revoked;
    if (license.isExpired) return _Status.expired;
    if (license.purchaseStatus == 'ACTIVE' || license.purchaseStatus == null) {
      return _Status.active;
    }
    return _Status.pending;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final (label, bg, fg) = switch (_status) {
      _Status.active => ('Active', cs.primaryContainer, cs.onPrimaryContainer),
      _Status.pending => (
          'Pending',
          cs.surfaceContainerHighest,
          cs.onSurfaceVariant
        ),
      _Status.expired => (
          'Expired',
          cs.surfaceContainerHighest,
          cs.onSurfaceVariant
        ),
      _Status.revoked => ('Revoked', cs.errorContainer, cs.onErrorContainer),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration:
          BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Text(label,
          style:
              TextStyle(color: fg, fontWeight: FontWeight.w700, fontSize: 12)),
    );
  }
}

class _AnchorBadge extends StatelessWidget {
  final License license;
  const _AnchorBadge({required this.license});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final anchor = license.anchor;
    if (anchor != null && anchor.isConfirmed) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.verified_user, size: 13, color: cs.primary),
        const SizedBox(width: 3),
        Text('On-chain', style: TextStyle(color: cs.primary, fontSize: 11)),
      ]);
    }
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.hourglass_empty, size: 13, color: cs.outline),
      const SizedBox(width: 3),
      Text('Anchoring…', style: TextStyle(color: cs.outline, fontSize: 11)),
    ]);
  }
}

class _EmptyLibrary extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      children: [
        const SizedBox(height: 90),
        Icon(Icons.apps_outlined, size: 56, color: cs.outline),
        const SizedBox(height: 12),
        Center(
          child:
              Text('No apps yet', style: Theme.of(context).textTheme.titleMedium),
        ),
        const SizedBox(height: 6),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(
            'Apps you buy from the Store show up here with their license and '
            'on-chain proof.',
            textAlign: TextAlign.center,
            style: TextStyle(color: cs.outline),
          ),
        ),
        const SizedBox(height: 16),
        Center(
          child: FilledButton.tonalIcon(
            icon: const Icon(Icons.storefront),
            label: const Text('Browse the Store'),
            onPressed: () => context.go('/store'),
          ),
        ),
      ],
    );
  }
}

class _LibraryError extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _LibraryError({required this.message, required this.onRetry});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.lock_outline, size: 48, color: cs.outline),
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
