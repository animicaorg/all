// NFTs — pulls the active wallet's collection from animica.xyz's marketplace API.
//
// API contract (already shipped):
//   GET https://animica.xyz/api/marketplace/profile/<addr>/nfts
//     → { items: [{ collectionAddress, tokenId, name, imageUrl, … }, ...] }
//
// If the API isn't available we fall back to an empty state with a CTA
// to open the marketplace.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import '../constants.dart';
import '../state/wallet_state.dart';

class NftSummary {
  final String collectionAddress;
  final String tokenId;
  final String? name;
  final String? imageUrl;
  const NftSummary({
    required this.collectionAddress,
    required this.tokenId,
    this.name,
    this.imageUrl,
  });

  static NftSummary fromJson(Map<String, dynamic> j) => NftSummary(
        collectionAddress: (j['collectionAddress'] ?? j['collection_address'] ?? '') as String,
        tokenId: (j['tokenId'] ?? j['token_id'] ?? '').toString(),
        name: j['name'] as String?,
        imageUrl: (j['imageUrl'] ?? j['image_url']) as String?,
      );
}

final _nftsProvider = FutureProvider.autoDispose<List<NftSummary>>((ref) async {
  final acc = ref.watch(activeAccountProvider);
  if (acc == null) return const [];
  final url = Uri.parse(
      '${AnimicaConfig.marketplaceUrl}/api/marketplace/profile/${Uri.encodeComponent(acc.address)}/nfts');
  try {
    final resp = await http.get(url).timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) return const [];
    final body = jsonDecode(resp.body);
    final items = (body is Map && body['items'] is List)
        ? body['items'] as List
        : (body is List ? body : const []);
    return items
        .whereType<Map<String, dynamic>>()
        .map(NftSummary.fromJson)
        .where((n) => n.collectionAddress.isNotEmpty)
        .toList(growable: false);
  } catch (_) {
    return const [];
  }
});

class NftsScreen extends ConsumerWidget {
  const NftsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final acc = ref.watch(activeAccountProvider);
    final nfts = ref.watch(_nftsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('NFTs'),
        actions: [
          IconButton(
            icon: const Icon(Icons.open_in_new),
            tooltip: 'Marketplace',
            onPressed: () => launchUrl(
              Uri.parse('${AnimicaConfig.marketplaceUrl}/marketplace'),
              mode: LaunchMode.externalApplication,
            ),
          ),
        ],
      ),
      body: acc == null
          ? const Center(child: Text('No active account.'))
          : nfts.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(child: Text('Marketplace API error:\n$e')),
              data: (items) {
                if (items.isEmpty) {
                  return _EmptyNftState();
                }
                return RefreshIndicator(
                  onRefresh: () async => ref.refresh(_nftsProvider.future),
                  child: GridView.builder(
                    padding: const EdgeInsets.all(12),
                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 2,
                      childAspectRatio: 0.78,
                      crossAxisSpacing: 12,
                      mainAxisSpacing: 12,
                    ),
                    itemCount: items.length,
                    itemBuilder: (c, i) => _NftTile(items[i]),
                  ),
                );
              },
            ),
    );
  }
}

class _NftTile extends StatelessWidget {
  final NftSummary nft;
  const _NftTile(this.nft);

  @override
  Widget build(BuildContext context) {
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => launchUrl(
          Uri.parse(
              '${AnimicaConfig.marketplaceUrl}/marketplace/nft/${nft.collectionAddress}:${nft.tokenId}'),
          mode: LaunchMode.externalApplication,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(
              child: nft.imageUrl == null
                  ? Container(
                      color: Theme.of(context).colorScheme.surfaceContainerHighest,
                      child: const Icon(Icons.image_not_supported, size: 32),
                    )
                  : Image.network(
                      nft.imageUrl!,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => Container(
                        color: Theme.of(context).colorScheme.surfaceContainerHighest,
                        child: const Icon(Icons.broken_image),
                      ),
                    ),
            ),
            Padding(
              padding: const EdgeInsets.all(8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    nft.name ?? '#${nft.tokenId}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    '${nft.collectionAddress.substring(0, 8)}…',
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EmptyNftState extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.collections_outlined,
                size: 56, color: Theme.of(context).colorScheme.outline),
            const SizedBox(height: 12),
            Text('No NFTs yet', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            Text(
              'Once you mint or buy an Animica NFT, it shows up here automatically.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Theme.of(context).colorScheme.outline),
            ),
            const SizedBox(height: 16),
            FilledButton.tonalIcon(
              icon: const Icon(Icons.travel_explore),
              label: const Text('Open marketplace'),
              onPressed: () => launchUrl(
                Uri.parse('${AnimicaConfig.marketplaceUrl}/marketplace'),
                mode: LaunchMode.externalApplication,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
