/// Statistics page with mining charts and graphs
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:fl_chart/fl_chart.dart';

import '../../state/miner_state.dart';
import '../../utils/formatters.dart';

class StatsPage extends ConsumerStatefulWidget {
  const StatsPage({super.key});

  @override
  ConsumerState<StatsPage> createState() => _StatsPageState();
}

class _StatsPageState extends ConsumerState<StatsPage> {
  final List<double> _hashrateHistory = [];
  static const int _maxDataPoints = 60; // Keep last 60 data points

  @override
  Widget build(BuildContext context) {
    final hashrate = ref.watch(hashrateProvider);
    final blocksFound = ref.watch(blocksFoundProvider);
    final sharesFound = ref.watch(sharesFoundProvider);

    // Add current hashrate to history
    if (_hashrateHistory.isEmpty || _hashrateHistory.last != hashrate) {
      _hashrateHistory.add(hashrate);
      if (_hashrateHistory.length > _maxDataPoints) {
        _hashrateHistory.removeAt(0);
      }
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mining Statistics'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () {
              setState(() {
                _hashrateHistory.clear();
              });
            },
            tooltip: 'Reset chart',
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Summary cards
            Row(
              children: [
                Expanded(
                  child: _buildStatCard(
                    context,
                    'Current Hashrate',
                    formatHashrate(hashrate),
                    Icons.speed,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _buildStatCard(
                    context,
                    'Blocks Found',
                    blocksFound.toString(),
                    Icons.inventory_2,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: _buildStatCard(
                    context,
                    'Shares Found',
                    sharesFound.toString(),
                    Icons.share,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _buildStatCard(
                    context,
                    'Avg Hashrate',
                    _hashrateHistory.isEmpty
                        ? '0 H/s'
                        : formatHashrate(
                            _hashrateHistory.reduce((a, b) => a + b) /
                                _hashrateHistory.length,
                          ),
                    Icons.trending_up,
                  ),
                ),
              ],
            ),

            const SizedBox(height: 24),

            // Hashrate chart
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Hashrate History',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 16),
                    SizedBox(
                      height: 300,
                      child: _hashrateHistory.isEmpty
                          ? const Center(
                              child: Text('No data yet'),
                            )
                          : LineChart(
                              _buildHashrateChart(),
                            ),
                    ),
                  ],
                ),
              ),
            ),

            const SizedBox(height: 16),

            // Additional stats
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Session Statistics',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 16),
                    _buildStatRow('Data Points', _hashrateHistory.length.toString()),
                    _buildStatRow('Peak Hashrate', _hashrateHistory.isEmpty
                        ? '0 H/s'
                        : formatHashrate(_hashrateHistory.reduce((a, b) => a > b ? a : b))),
                    _buildStatRow('Min Hashrate', _hashrateHistory.isEmpty
                        ? '0 H/s'
                        : formatHashrate(_hashrateHistory.reduce((a, b) => a < b ? a : b))),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatCard(BuildContext context, String title, String value, IconData icon) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 32, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 8),
            Text(
              title,
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 4),
            Text(
              value,
              style: Theme.of(context).textTheme.titleLarge?.copyWith(
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label),
          Text(
            value,
            style: const TextStyle(fontWeight: FontWeight.bold),
          ),
        ],
      ),
    );
  }

  LineChartData _buildHashrateChart() {
    final spots = <FlSpot>[];
    for (int i = 0; i < _hashrateHistory.length; i++) {
      spots.add(FlSpot(i.toDouble(), _hashrateHistory[i]));
    }

    return LineChartData(
      gridData: FlGridData(
        show: true,
        drawVerticalLine: true,
        horizontalInterval: _hashrateHistory.isEmpty
            ? 1
            : _hashrateHistory.reduce((a, b) => a > b ? a : b) / 5,
      ),
      titlesData: FlTitlesData(
        show: true,
        rightTitles: const AxisTitles(
          sideTitles: SideTitles(showTitles: false),
        ),
        topTitles: const AxisTitles(
          sideTitles: SideTitles(showTitles: false),
        ),
        bottomTitles: AxisTitles(
          sideTitles: SideTitles(
            showTitles: true,
            reservedSize: 30,
            interval: _maxDataPoints / 5,
            getTitlesWidget: (value, meta) {
              return Text(
                value.toInt().toString(),
                style: const TextStyle(fontSize: 10),
              );
            },
          ),
        ),
        leftTitles: AxisTitles(
          sideTitles: SideTitles(
            showTitles: true,
            reservedSize: 60,
            getTitlesWidget: (value, meta) {
              return Text(
                formatHashrate(value),
                style: const TextStyle(fontSize: 10),
              );
            },
          ),
        ),
      ),
      borderData: FlBorderData(
        show: true,
        border: Border.all(color: Colors.grey.withOpacity(0.2)),
      ),
      minX: 0,
      maxX: (_maxDataPoints - 1).toDouble(),
      minY: 0,
      maxY: _hashrateHistory.isEmpty
          ? 100
          : _hashrateHistory.reduce((a, b) => a > b ? a : b) * 1.1,
      lineBarsData: [
        LineChartBarData(
          spots: spots,
          isCurved: true,
          color: Theme.of(context).colorScheme.primary,
          barWidth: 3,
          isStrokeCapRound: true,
          dotData: const FlDotData(show: false),
          belowBarData: BarAreaData(
            show: true,
            color: Theme.of(context).colorScheme.primary.withOpacity(0.1),
          ),
        ),
      ],
    );
  }
}
