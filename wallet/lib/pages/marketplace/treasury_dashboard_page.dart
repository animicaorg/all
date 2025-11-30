/// Treasury Status Dashboard
/// 
/// Displays:
/// - Current treasury balance and allocation
/// - Revenue progress toward $1B target
/// - Pricing trends
/// - Sales velocity
/// - Projections

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/providers.dart';
import '../../widgets/chart_widget.dart';

class TreasuryDashboardPage extends ConsumerWidget {
  const TreasuryDashboardPage({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final treasuryAsync = ref.watch(treasurySnapshotProvider);
    final revenueAsync = ref.watch(treasuryRevenueProvider);
    final priceAsync = ref.watch(anmPriceProvider);
    final yearsToTargetAsync = ref.watch(yearsToTargetProvider);
    final eoySimAsync = ref.watch(eoySimulationProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Treasury Dashboard'),
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await ref.refresh(treasurySnapshotProvider.future);
          await ref.refresh(treasuryRevenueProvider.future);
        },
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Revenue progress to $1B
              treasuryAsync.when(
                data: (treasury) => revenueAsync.when(
                  data: (revenue) => _buildProgressCard(context, revenue, treasury),
                  loading: () => const _LoadingCard(),
                  error: (e, st) => const SizedBox(),
                ),
                loading: () => const _LoadingCard(),
                error: (e, st) => const SizedBox(),
              ),

              const SizedBox(height: 24),

              // Key metrics grid
              Row(
                children: [
                  Expanded(
                    child: priceAsync.when(
                      data: (price) => _buildMetricCard(
                        'ANM Price',
                        '\$${price.toStringAsFixed(2)}',
                        Icons.trending_up,
                      ),
                      loading: () => const _MetricCardLoading(),
                      error: (e, st) => const SizedBox(),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: treasuryAsync.when(
                      data: (treasury) => _buildMetricCard(
                        'Sold',
                        '${treasury.percentSold.toStringAsFixed(1)}%',
                        Icons.done_all,
                      ),
                      loading: () => const _MetricCardLoading(),
                      error: (e, st) => const SizedBox(),
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 12),

              Row(
                children: [
                  Expanded(
                    child: yearsToTargetAsync.when(
                      data: (years) => _buildMetricCard(
                        'Years to \$1B',
                        years.toStringAsFixed(1),
                        Icons.schedule,
                      ),
                      loading: () => const _MetricCardLoading(),
                      error: (e, st) => const SizedBox(),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: treasuryAsync.when(
                      data: (treasury) => _buildMetricCard(
                        'Remaining',
                        '${(treasury.treasuryBalance / 1e9).toStringAsFixed(2)}B ANM',
                        Icons.storage,
                      ),
                      loading: () => const _MetricCardLoading(),
                      error: (e, st) => const SizedBox(),
                    ),
                  ),
                ],
              ),

              const SizedBox(height: 24),

              // Supply allocation breakdown
              Text(
                'Supply Allocation',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),

              treasuryAsync.when(
                data: (treasury) => _buildSupplyPie(context, treasury),
                loading: () => const SizedBox(height: 200, child: CircularProgressIndicator()),
                error: (e, st) => const SizedBox(),
              ),

              const SizedBox(height: 24),

              // Price history chart
              Text(
                'Price History (7 days)',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),

              ref.watch(priceHistoryProvider).when(
                data: (history) => _buildPriceChart(history),
                loading: () => const SizedBox(height: 250, child: CircularProgressIndicator()),
                error: (e, st) => const SizedBox(),
              ),

              const SizedBox(height: 24),

              // EOY projection
              Text(
                'End of Year Projection',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),

              eoySimAsync.when(
                data: (sim) => _buildProjectionCard(context, sim),
                loading: () => const _LoadingCard(),
                error: (e, st) => const SizedBox(),
              ),

              const SizedBox(height: 24),

              // Sale velocity
              Text(
                'Recent Sales Velocity',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),

              _buildVelocityTable(context),

              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }

  /// Progress card to $1B target
  Widget _buildProgressCard(
    BuildContext context,
    double revenue,
    TreasurySnapshot treasury,
  ) {
    const targetRevenue = 1e9;
    final percentToTarget = (revenue / targetRevenue * 100).clamp(0, 100);
    final remainingToTarget = (targetRevenue - revenue).clamp(0, double.infinity);

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [const Color(0xFF5EEAD4), const Color(0xFF2DD4BF)],
        ),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Treasury Revenue Progress',
            style: TextStyle(color: Colors.white70, fontSize: 14),
          ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                '\$${(revenue / 1e9).toStringAsFixed(2)}B',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 28,
                  fontWeight: FontWeight.bold,
                ),
              ),
              Text(
                '${percentToTarget.toStringAsFixed(1)}%',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 28,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: percentToTarget / 100,
              minHeight: 8,
              backgroundColor: Colors.white24,
              valueColor: AlwaysStoppedAnimation(
                percentToTarget >= 100 ? Colors.lightGreen : Colors.white,
              ),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            'Remaining: \$${(remainingToTarget / 1e9).toStringAsFixed(2)}B / \$1.00B Target',
            style: const TextStyle(color: Colors.white70, fontSize: 13),
          ),
        ],
      ),
    );
  }

  /// Metric card
  Widget _buildMetricCard(
    String label,
    String value,
    IconData icon,
  ) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  label,
                  style: const TextStyle(color: Colors.grey, fontSize: 12),
                ),
                Icon(icon, size: 16, color: Colors.grey),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              value,
              style: const TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Supply pie chart
  Widget _buildSupplyPie(BuildContext context, TreasurySnapshot treasury) {
    final sold = treasury.soldToDate;
    final remaining = treasury.treasuryBalance;
    final total = treasury.totalSupply;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _buildLegendItem('Sold', sold / total, const Color(0xFF5EEAD4)),
              _buildLegendItem('In Treasury', remaining / total, const Color(0xFF0F766E)),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _buildAllocationRow('Sold', sold, total),
              _buildAllocationRow('Treasury', remaining, total),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildLegendItem(String label, double percent, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 8),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: const TextStyle(fontSize: 12)),
            Text(
              '${(percent * 100).toStringAsFixed(1)}%',
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildAllocationRow(String label, double amount, double total) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
        Text(
          '${(amount / 1e9).toStringAsFixed(2)}B ANM',
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold),
        ),
      ],
    );
  }

  /// Price history chart
  Widget _buildPriceChart(List<double> history) {
    if (history.isEmpty) {
      return Container(
        height: 250,
        decoration: BoxDecoration(
          color: Colors.grey[100],
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Center(
          child: Text('No price history available'),
        ),
      );
    }

    return Container(
      height: 250,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Center(
              child: LineChart(history),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Min: \$${history.reduce((a, b) => a < b ? a : b).toStringAsFixed(2)}',
                style: const TextStyle(fontSize: 12, color: Colors.grey),
              ),
              Text(
                'Max: \$${history.reduce((a, b) => a > b ? a : b).toStringAsFixed(2)}',
                style: const TextStyle(fontSize: 12, color: Colors.grey),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// EOY projection card
  Widget _buildProjectionCard(BuildContext context, PricingSimulation sim) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(color: Colors.grey[300]!),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildProjectionRow('Current Price', '\$${sim.currentPrice.toStringAsFixed(4)}'),
          _buildProjectionRow('EOY Price', '\$${sim.endOfYearPrice.toStringAsFixed(4)}'),
          _buildProjectionRow(
            'EOY Revenue',
            '\$${(sim.revenueAtEoy / 1e9).toStringAsFixed(2)}B',
          ),
          _buildProjectionRow(
            'Remaining Supply',
            '${(sim.remainingSupply / 1e9).toStringAsFixed(2)}B ANM',
          ),
          const Divider(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                sim.reachesTarget ? '✓ Reaches \$1B target' : '○ Below \$1B target',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: sim.reachesTarget ? Colors.green : Colors.orange,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildProjectionRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: Colors.grey)),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }

  /// Sales velocity table
  Widget _buildVelocityTable(BuildContext context) {
    const velocityData = [
      ('Last 7 days', '125.5M ANM', '\$412.5M'),
      ('Last 30 days', '487.2M ANM', '\$1.62B'),
      ('Last 90 days', '1.2B ANM', '\$4.1B'),
    ];

    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: Colors.grey[300]!),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          ...velocityData.asMap().entries.map((e) {
            final (label, amount, revenue) = e.value;
            return Column(
              children: [
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(label, style: const TextStyle(fontSize: 13)),
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(amount, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold)),
                          Text(revenue, style: const TextStyle(fontSize: 11, color: Colors.grey)),
                        ],
                      ),
                    ],
                  ),
                ),
                if (e.key != velocityData.length - 1) const Divider(height: 0),
              ],
            );
          }),
        ],
      ),
    );
  }
}

// ============================================================================
// HELPER WIDGETS
// ============================================================================

class _LoadingCard extends StatelessWidget {
  const _LoadingCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(12),
      ),
      child: const Center(
        child: CircularProgressIndicator(),
      ),
    );
  }
}

class _MetricCardLoading extends StatelessWidget {
  const _MetricCardLoading();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(width: 80, height: 12, color: Colors.grey[300]),
            const SizedBox(height: 8),
            Container(width: 100, height: 20, color: Colors.grey[300]),
          ],
        ),
      ),
    );
  }
}

/// Simple line chart widget
class LineChart extends StatelessWidget {
  final List<double> data;

  const LineChart(this.data);

  @override
  Widget build(BuildContext context) {
    if (data.length < 2) return const SizedBox();

    final min = data.reduce((a, b) => a < b ? a : b);
    final max = data.reduce((a, b) => a > b ? a : b);
    final range = max - min;

    return CustomPaint(
      painter: _LineChartPainter(data, min, range),
      size: Size.infinite,
    );
  }
}

class _LineChartPainter extends CustomPainter {
  final List<double> data;
  final double min;
  final double range;

  _LineChartPainter(this.data, this.min, this.range);

  @override
  void paint(Canvas canvas, Size size) {
    if (data.isEmpty) return;

    final paint = Paint()
      ..color = const Color(0xFF5EEAD4)
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;

    final points = <Offset>[];
    for (int i = 0; i < data.length; i++) {
      final x = (i / (data.length - 1)) * size.width;
      final normalized = range > 0 ? (data[i] - min) / range : 0;
      final y = size.height * (1 - normalized);
      points.add(Offset(x, y));
    }

    for (int i = 0; i < points.length - 1; i++) {
      canvas.drawLine(points[i], points[i + 1], paint);
    }
  }

  @override
  bool shouldRepaint(_LineChartPainter oldDelegate) => false;
}
