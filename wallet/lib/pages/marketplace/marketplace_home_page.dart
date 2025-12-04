/// Marketplace Home / Dashboard
/// 
/// Main hub for token purchases:
/// - Quick buy CTA
/// - Real-time price ticker
/// - Portfolio summary
/// - Treasury progress
/// - Quick links to other sections

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../../services/pricing_engine.dart' show MarketPriceData;

class MarketplaceHomePage extends ConsumerWidget {
  const MarketplaceHomePage({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dashboardAsync = ref.watch(dashboardSummaryProvider);
    final priceUpdatesStream = ref.watch(priceUpdatesStreamProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('ANM Marketplace'),
        elevation: 0,
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await ref.refresh(dashboardSummaryProvider.future);
        },
        child: CustomScrollView(
          slivers: [
            // Hero price card
            SliverToBoxAdapter(
              child: dashboardAsync.when(
                data: (dashboard) => _buildPriceHero(
                  context,
                  dashboard,
                  priceUpdatesStream,
                ),
                loading: () => const _PriceHeroLoading(),
                error: (e, st) => const SizedBox(),
              ),
            ),

            // Quick action buttons
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: _buildQuickActions(context),
              ),
            ),

            // Portfolio overview
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: dashboardAsync.when(
                  data: (dashboard) => _buildPortfolioCard(context, dashboard),
                  loading: () => const SizedBox(),
                  error: (e, st) => const SizedBox(),
                ),
              ),
            ),

            const SliverToBoxAdapter(child: SizedBox(height: 24)),

            // Treasury progress
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: dashboardAsync.when(
                  data: (dashboard) => _buildTreasuryProgress(context, dashboard),
                  loading: () => const SizedBox(),
                  error: (e, st) => const SizedBox(),
                ),
              ),
            ),

            const SliverToBoxAdapter(child: SizedBox(height: 24)),

            // Market insights
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: _buildMarketInsights(context, ref),
              ),
            ),

            const SliverToBoxAdapter(child: SizedBox(height: 32)),
          ],
        ),
      ),
    );
  }

  /// Price hero card with live ticker
  Widget _buildPriceHero(
    BuildContext context,
    DashboardSummary dashboard,
    AsyncValue<MarketPriceData> priceUpdates,
  ) {
    return Container(
      margin: const EdgeInsets.all(16),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF5EEAD4),
            const Color(0xFF2DD4BF),
            const Color(0xFF0F766E),
          ],
        ),
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFF5EEAD4).withOpacity(0.3),
            blurRadius: 20,
            offset: const Offset(0, 10),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'ANM Token Price',
                    style: TextStyle(
                      color: Colors.white70,
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '\$${dashboard.anmPrice.toStringAsFixed(4)}',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 48,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: dashboard.priceChange24h > 0
                      ? Colors.lightGreen
                      : Colors.redAccent,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  children: [
                    Icon(
                      dashboard.priceChange24h > 0
                          ? Icons.trending_up
                          : Icons.trending_down,
                      color: Colors.white,
                      size: 24,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${dashboard.priceChange24h > 0 ? '+' : ''}${dashboard.priceChange24h.toStringAsFixed(2)}%',
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),

          const SizedBox(height: 24),

          // Live update indicator
          priceUpdates.when(
            data: (price) => _buildUpdateIndicator(price),
            loading: () => const SizedBox(),
            error: (e, st) => const SizedBox(),
          ),

          const SizedBox(height: 16),

          // CTA Button
          SizedBox(
            width: double.infinity,
            height: 48,
            child: ElevatedButton.icon(
              onPressed: () => context.push('/marketplace/buy'),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: const Color(0xFF0F766E),
              ),
              icon: const Icon(Icons.shopping_cart),
              label: const Text('Buy ANM Now'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildUpdateIndicator(MarketPriceData price) {
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: const BoxDecoration(
            color: Colors.lightGreen,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 8),
        Text(
          'Updated from ${price.source} • Live',
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 12,
          ),
        ),
      ],
    );
  }

  /// Quick action buttons
  Widget _buildQuickActions(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _buildActionButton(
            context,
            icon: Icons.history,
            label: 'History',
            onTap: () => context.push('/marketplace/history'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildActionButton(
            context,
            icon: Icons.account_balance,
            label: 'Treasury',
            onTap: () => context.push('/marketplace/treasury'),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildActionButton(
            context,
            icon: Icons.analytics,
            label: 'Analytics',
            onTap: () => context.push('/marketplace/analytics'),
          ),
        ),
      ],
    );
  }

  Widget _buildActionButton(
    BuildContext context, {
    required IconData icon,
    required String label,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          border: Border.all(color: Colors.grey[300]!),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          children: [
            Icon(icon, color: const Color(0xFF5EEAD4)),
            const SizedBox(height: 4),
            Text(
              label,
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }

  /// Portfolio overview card
  Widget _buildPortfolioCard(BuildContext context, DashboardSummary dashboard) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(color: Colors.grey[200]!),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Your Portfolio',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              Icon(Icons.account_balance_wallet, color: Colors.grey[400]),
            ],
          ),
          const SizedBox(height: 16),

          // Balance
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'ANM Balance',
                style: TextStyle(
                  fontSize: 12,
                  color: Colors.grey[600],
                ),
              ),
              const SizedBox(height: 4),
              Text(
                '${dashboard.anmBalance.toStringAsFixed(4)} ANM',
                style: const TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),

          const SizedBox(height: 16),
          const Divider(),
          const SizedBox(height: 16),

          // Portfolio value
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Portfolio Value',
                    style: TextStyle(fontSize: 12, color: Colors.grey[600]),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '\$${dashboard.portfolioValue.toStringAsFixed(2)}',
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      color: Colors.green,
                    ),
                  ),
                ],
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    'Avg. Price',
                    style: TextStyle(fontSize: 12, color: Colors.grey[600]),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '\$${(dashboard.portfolioValue / dashboard.anmBalance).toStringAsFixed(4)}',
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: Colors.blue[700],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Treasury progress card
  Widget _buildTreasuryProgress(BuildContext context, DashboardSummary dashboard) {
    final progressPercent = dashboard.percentToTarget;
    final isOnTrack = dashboard.yearsToTarget < 5;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.amber[50],
        border: Border.all(color: Colors.amber[200]!),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Treasury to \$1B',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: isOnTrack ? Colors.green[100] : Colors.orange[100],
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  isOnTrack ? '✓ On track' : '⚠ Slower pace',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: isOnTrack ? Colors.green[700] : Colors.orange[700],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Progress bar
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    '\$${(dashboard.treasuryRevenue / 1e9).toStringAsFixed(2)}B',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  Text(
                    '${progressPercent.toStringAsFixed(1)}%',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: progressPercent / 100,
                  minHeight: 10,
                  backgroundColor: Colors.grey[300],
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Years to target: ${dashboard.yearsToTarget.toStringAsFixed(1)}',
                style: TextStyle(fontSize: 12, color: Colors.grey[700]),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// Market insights section
  Widget _buildMarketInsights(BuildContext context, WidgetRef ref) {
    final priceHistoryAsync = ref.watch(priceHistoryProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Market Insights',
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 12),
        priceHistoryAsync.when(
          data: (history) => _buildInsightCards(history),
          loading: () => const SizedBox(height: 100, child: CircularProgressIndicator()),
          error: (e, st) => const SizedBox(),
        ),
      ],
    );
  }

  Widget _buildInsightCards(List<double> history) {
    if (history.isEmpty) {
      return const SizedBox();
    }

    final min = history.reduce((a, b) => a < b ? a : b);
    final max = history.reduce((a, b) => a > b ? a : b);
    final avg = history.isNotEmpty ? history.reduce((a, b) => a + b) / history.length : 0;

    return Row(
      children: [
        Expanded(
          child: _buildInsightCard('7D Low', '\$${min.toStringAsFixed(2)}', Colors.blue),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildInsightCard('7D Avg', '\$${avg.toStringAsFixed(2)}', Colors.teal),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildInsightCard('7D High', '\$${max.toStringAsFixed(2)}', Colors.green),
        ),
      ],
    );
  }

  Widget _buildInsightCard(String label, String value, Color color) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        border: Border.all(color: color.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontSize: 11, color: Colors.grey)),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold),
          ),
        ],
      ),
    );
  }
}

// ============================================================================
// LOADING STATES
// ============================================================================

class _PriceHeroLoading extends StatelessWidget {
  const _PriceHeroLoading();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(16),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: Colors.grey[200],
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(width: 120, height: 16, color: Colors.grey[300]),
          const SizedBox(height: 12),
          Container(width: 200, height: 40, color: Colors.grey[300]),
          const SizedBox(height: 24),
          Container(width: double.infinity, height: 48, color: Colors.grey[300]),
        ],
      ),
    );
  }
}
