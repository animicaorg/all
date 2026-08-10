// DEX home — token discovery (search + promoted carousel) and the per-token
// detail page (chart, market stats, swap/liquidity/promote entry points).
//
// Honest by design: discovery and market data come from the off-chain token
// index (empty states when none is configured), and the capability banner
// flags networks where contract execution isn't live yet.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../services/price.dart';
import '../../state/dex_state.dart';
import '../../state/wallet_state.dart';
import '../../widgets/price_chart.dart';
import 'dex_common.dart';

const _kUp = Color(0xFF16A34A);
const _kDown = Color(0xFFDC2626);

String _shortAddr(String a) =>
    a.length <= 20 ? a : '${a.substring(0, 12)}…${a.substring(a.length - 6)}';

String _changeText(double c) => '${c >= 0 ? '+' : ''}${c.toStringAsFixed(2)}%';

Color _changeColor(double c) => c >= 0 ? _kUp : _kDown;

void _copy(BuildContext context, String text, String what) {
  Clipboard.setData(ClipboardData(text: text));
  ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text('$what copied')));
}

// ---------------------------------------------------------------------------
// Home: search + promoted carousel + token list
// ---------------------------------------------------------------------------

class DexHomeScreen extends ConsumerStatefulWidget {
  const DexHomeScreen({super.key});

  @override
  ConsumerState<DexHomeScreen> createState() => _DexHomeScreenState();
}

class _DexHomeScreenState extends ConsumerState<DexHomeScreen> {
  final _searchCtl = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchCtl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final q = _query.trim();
    final market = ref.watch(anmMarketProvider).value;
    final featured = ref.watch(featuredTokensProvider);
    final results = q.isEmpty ? featured : ref.watch(tokenSearchProvider(q));
    final promoted = featured.value ?? const <TokenInfo>[];
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Tokens & DEX'),
        actions: [
          IconButton(
            icon: const Icon(Icons.rocket_launch_outlined),
            tooltip: 'Launch a token',
            onPressed: () => context.push('/dex/launch'),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => context.push('/dex/launch'),
        icon: const Icon(Icons.rocket_launch),
        label: const Text('Launch token'),
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(featuredTokensProvider);
          if (q.isNotEmpty) ref.invalidate(tokenSearchProvider(q));
          await ref.read(
              q.isEmpty ? featuredTokensProvider.future : tokenSearchProvider(q).future);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 96),
          children: [
            const DexCapabilityBanner(),
            TextField(
              controller: _searchCtl,
              decoration: InputDecoration(
                hintText: 'Search name, symbol or anim1… address',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: q.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear),
                        tooltip: 'Clear',
                        onPressed: () {
                          _searchCtl.clear();
                          setState(() => _query = '');
                        },
                      ),
                isDense: true,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
              onChanged: (v) => setState(() => _query = v),
            ),
            const SizedBox(height: 16),
            if (promoted.isNotEmpty) ...[
              Text('Promoted', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              SizedBox(
                height: 148,
                child: ListView.builder(
                  scrollDirection: Axis.horizontal,
                  itemCount: promoted.length,
                  itemBuilder: (_, i) => _PromotedCard(info: promoted[i]),
                ),
              ),
              const SizedBox(height: 16),
            ],
            Text(q.isEmpty ? 'Tokens' : 'Results',
                style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 4),
            results.when(
              loading: () => const Padding(
                padding: EdgeInsets.symmetric(vertical: 32),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (e, _) => Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: cs.errorContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text('Token lookup failed: $e',
                    style: TextStyle(color: cs.onErrorContainer)),
              ),
              data: (list) => list.isEmpty
                  ? _EmptyDiscovery(query: q)
                  : Column(
                      children: [
                        for (final info in list)
                          _TokenTile(info: info, market: market),
                      ],
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PromotedCard extends StatelessWidget {
  final TokenInfo info;
  const _PromotedCard({required this.info});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      width: 132,
      child: Card(
        margin: const EdgeInsets.only(right: 10, bottom: 4),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => context.push('/dex/token/${info.address}'),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _TokenAvatar(info: info, size: 36),
                const SizedBox(height: 8),
                Text(
                  info.symbol,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
                ),
                const SizedBox(height: 2),
                Text(
                  anmAmount(info.priceAnm),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: cs.outline, fontSize: 11.5),
                ),
                if (info.promoDaysLeft != null)
                  Text(
                    '${info.promoDaysLeft}d left',
                    style: TextStyle(color: cs.tertiary, fontSize: 10.5),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TokenTile extends StatelessWidget {
  final TokenInfo info;
  final AnmMarket? market;
  const _TokenTile({required this.info, required this.market});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ch = info.change24h;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 4),
      leading: _TokenAvatar(info: info, size: 40),
      title: Text(
        info.name.isEmpty ? info.symbol : info.name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(info.symbol, style: TextStyle(color: cs.outline, fontSize: 12)),
      trailing: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 150),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              priceLine(info.priceAnm, market),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12.5),
            ),
            if (ch != null)
              Text(
                _changeText(ch),
                style: TextStyle(color: _changeColor(ch), fontSize: 12),
              ),
          ],
        ),
      ),
      onTap: () => context.push('/dex/token/${info.address}'),
    );
  }
}

class _TokenAvatar extends StatelessWidget {
  final TokenInfo info;
  final double size;
  const _TokenAvatar({required this.info, required this.size});

  @override
  Widget build(BuildContext context) {
    final url = info.imageUrl;
    final fallback = SizedBox(
      width: size,
      height: size,
      child: CircleAvatar(
        child: Text(
          info.symbol.isEmpty ? '?' : info.symbol[0].toUpperCase(),
          style: TextStyle(fontSize: size * 0.4, fontWeight: FontWeight.w700),
        ),
      ),
    );
    if (url == null || url.isEmpty) return fallback;
    return ClipOval(
      child: Image.network(
        url,
        width: size,
        height: size,
        fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => fallback,
      ),
    );
  }
}

class _EmptyDiscovery extends StatelessWidget {
  final String query;
  const _EmptyDiscovery({required this.query});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final looksLikeAddress = query.startsWith('anim1') && query.length >= 20;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Icon(Icons.travel_explore, size: 36, color: cs.outline),
            const SizedBox(height: 10),
            const Text('No tokens to show',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
            const SizedBox(height: 6),
            Text(
              'Token discovery needs a token indexer, and none is configured for '
              'this network yet (or nothing matched). You can still open any '
              'token directly by its contract address.',
              textAlign: TextAlign.center,
              style: TextStyle(color: cs.outline, fontSize: 12.5),
            ),
            const SizedBox(height: 14),
            if (looksLikeAddress)
              FilledButton.tonalIcon(
                icon: const Icon(Icons.open_in_new, size: 18),
                label: Text('Open ${_shortAddr(query)}'),
                onPressed: () => context.push('/dex/token/$query'),
              )
            else
              Text(
                'Paste an anim1… contract address above to open it directly.',
                textAlign: TextAlign.center,
                style: TextStyle(color: cs.outline, fontSize: 11.5),
              ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Token detail: chart + stats + swap/liquidity/promote
// ---------------------------------------------------------------------------

class TokenDetailScreen extends ConsumerWidget {
  final String address;
  const TokenDetailScreen({super.key, required this.address});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final infoAsync = ref.watch(tokenInfoProvider(address));
    final historyAsync = ref.watch(tokenHistoryProvider(address));
    final market = ref.watch(anmMarketProvider).value;

    final symbol = infoAsync.value?.symbol ?? '';
    return Scaffold(
      appBar: AppBar(title: Text(symbol.isEmpty ? 'Token' : symbol)),
      body: infoAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (_, __) => _NotIndexedBody(address: address),
        data: (info) => info == null
            ? _NotIndexedBody(address: address)
            : _DetailBody(info: info, history: historyAsync, market: market),
      ),
    );
  }
}

class _NotIndexedBody extends StatelessWidget {
  final String address;
  const _NotIndexedBody({required this.address});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        const DexCapabilityBanner(transferStillWorks: true),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              children: [
                Icon(Icons.help_outline, size: 36, color: cs.outline),
                const SizedBox(height: 10),
                const Text('Token not indexed yet',
                    style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                const SizedBox(height: 6),
                Text(
                  'This token isn\'t in the token index (or no index is '
                  'configured for this network), so its name, price and chart '
                  'aren\'t available here. On-chain actions by address still work.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: cs.outline, fontSize: 12.5),
                ),
                const SizedBox(height: 14),
                _AddressRow(address: address),
                const SizedBox(height: 14),
                FilledButton.icon(
                  icon: const Icon(Icons.campaign_outlined, size: 18),
                  label: const Text('Promote this token'),
                  onPressed: () => context.push('/dex/promote/$address'),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _DetailBody extends StatelessWidget {
  final TokenInfo info;
  final AsyncValue<List<PricePoint>> history;
  final AnmMarket? market;
  const _DetailBody({required this.info, required this.history, required this.market});

  static String _chartLabel(double v) => '${v.toStringAsFixed(6)} ANM';

  static IconData _linkIcon(String key) {
    switch (key.toLowerCase()) {
      case 'website':
      case 'web':
      case 'site':
        return Icons.language;
      case 'x':
      case 'twitter':
        return Icons.alternate_email;
      case 'telegram':
        return Icons.send_outlined;
      case 'discord':
        return Icons.forum_outlined;
      case 'github':
        return Icons.code;
      default:
        return Icons.link;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ch = info.change24h;
    final feeBps = info.feeBps;
    final description = info.description ?? '';

    String? usdSub(double? anm) {
      final m = market;
      if (anm == null || m == null) return null;
      return formatUsd(anm * m.usdPerAnm, sigFigs: 3);
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        const DexCapabilityBanner(transferStillWorks: true),

        // Header: image, name/symbol, promoted badge, copyable address.
        Row(
          children: [
            _TokenAvatar(info: info, size: 48),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    info.name.isEmpty ? info.symbol : info.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text(info.symbol,
                      style: TextStyle(color: cs.outline, fontSize: 13)),
                ],
              ),
            ),
            if (info.promoted)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: cs.tertiaryContainer,
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  info.promoDaysLeft != null
                      ? 'Promoted · ${info.promoDaysLeft}d'
                      : 'Promoted',
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      color: cs.onTertiaryContainer),
                ),
              ),
          ],
        ),
        const SizedBox(height: 4),
        Align(
          alignment: Alignment.centerLeft,
          child: _AddressRow(address: info.address),
        ),
        const SizedBox(height: 12),

        // Price chart.
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: history.when(
              loading: () => const SizedBox(
                height: 180,
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (_, __) =>
                  const PriceChart(points: [], label: _chartLabel),
              data: (pts) => PriceChart(points: pts, label: _chartLabel),
            ),
          ),
        ),
        const SizedBox(height: 12),

        // Market stats.
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            child: Column(
              children: [
                StatRow(label: 'Price', value: priceLine(info.priceAnm, market)),
                StatRow(
                  label: 'Market cap',
                  value: anmAmount(info.marketCapAnm),
                  sub: usdSub(info.marketCapAnm),
                ),
                StatRow(
                  label: 'Liquidity',
                  value: anmAmount(info.liquidityAnm),
                  sub: usdSub(info.liquidityAnm),
                ),
                StatRow(
                  label: '24h change',
                  value: ch == null ? '—' : _changeText(ch),
                  valueColor: ch == null ? null : _changeColor(ch),
                ),
                StatRow(
                  label: 'Swap fee',
                  value: feeBps == null
                      ? '—'
                      : '${(feeBps / 100).toStringAsFixed(2)}%',
                ),
              ],
            ),
          ),
        ),
        if (info.pairAddress == null || info.liquidityAnm == null)
          Padding(
            padding: const EdgeInsets.only(top: 6, left: 4),
            child: Text(
              info.pairAddress == null
                  ? 'No ANM liquidity pool yet.'
                  : 'Pool data unavailable on this network yet.',
              style: TextStyle(color: cs.outline, fontSize: 11.5),
            ),
          ),
        const SizedBox(height: 16),

        // Actions.
        Row(
          children: [
            Expanded(
              child: FilledButton.icon(
                icon: const Icon(Icons.swap_horiz, size: 18),
                label: const Text('Swap'),
                style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8)),
                onPressed: () =>
                    context.push('/dex/swap?token=${info.address}'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                icon: const Icon(Icons.water_drop_outlined, size: 18),
                label: const Text('Liquidity'),
                style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8)),
                onPressed: () => context.push('/dex/liquidity'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                icon: const Icon(Icons.campaign_outlined, size: 18),
                label: const Text('Promote'),
                style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8)),
                onPressed: () =>
                    context.push('/dex/promote/${info.address}'),
              ),
            ),
          ],
        ),

        // Links (copy-to-clipboard; no url_launcher dependency).
        if (info.links.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text('Links', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final e in info.links.entries)
                OutlinedButton.icon(
                  icon: Icon(_linkIcon(e.key), size: 16),
                  label: Text(e.key),
                  onPressed: () => _copy(context, e.value, '${e.key} link'),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text('Tap a link to copy its URL.',
              style: TextStyle(color: cs.outline, fontSize: 11)),
        ],

        // Description.
        if (description.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text('About', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          Text(description, style: const TextStyle(fontSize: 13.5, height: 1.35)),
        ],
      ],
    );
  }
}

class _AddressRow extends StatelessWidget {
  final String address;
  const _AddressRow({required this.address});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: () => _copy(context, address, 'Address'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                _shortAddr(address),
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontFamily: 'monospace', fontSize: 12.5, color: cs.outline),
              ),
            ),
            const SizedBox(width: 6),
            Icon(Icons.copy, size: 14, color: cs.outline),
          ],
        ),
      ),
    );
  }
}
