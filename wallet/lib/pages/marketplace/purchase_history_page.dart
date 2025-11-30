/// Purchase History & Portfolio Page
/// 
/// Shows:
/// - All ANM purchases
/// - Purchase details and receipts
/// - Total balance & average price
/// - Tax reporting
/// - Export options

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../state/providers.dart';
import '../../services/payment_gateway.dart';

class PurchaseHistoryPage extends ConsumerWidget {
  const PurchaseHistoryPage({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final historyAsync = ref.watch(purchaseHistoryProvider);
    final balanceAsync = ref.watch(anmBalanceProvider);
    final spentAsync = ref.watch(totalSpentProvider);
    final avgPriceAsync = ref.watch(averagePurchasePriceProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Purchase History'),
        actions: [
          PopupMenuButton(
            itemBuilder: (context) => [
              const PopupMenuItem(
                child: Text('Export CSV'),
              ),
              const PopupMenuItem(
                child: Text('Export PDF'),
              ),
              const PopupMenuItem(
                child: Text('Share Receipt'),
              ),
            ],
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await ref.refresh(purchaseHistoryProvider.future);
          await ref.refresh(anmBalanceProvider.future);
        },
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Summary cards
              historyAsync.when(
                data: (history) => _buildSummaryCards(
                  context,
                  balanceAsync,
                  spentAsync,
                  avgPriceAsync,
                  history.length,
                ),
                loading: () => const SizedBox(height: 120, child: CircularProgressIndicator()),
                error: (e, st) => const SizedBox(),
              ),

              const SizedBox(height: 32),

              // Purchase list
              Text(
                'Purchase History',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 16),

              historyAsync.when(
                data: (history) {
                  if (history.isEmpty) {
                    return Center(
                      child: Padding(
                        padding: const EdgeInsets.all(32),
                        child: Column(
                          children: [
                            Icon(Icons.history, size: 64, color: Colors.grey[300]),
                            const SizedBox(height: 16),
                            Text(
                              'No purchases yet',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 8),
                            const Text(
                              'Start buying ANM tokens to build your portfolio',
                              textAlign: TextAlign.center,
                            ),
                          ],
                        ),
                      ),
                    );
                  }

                  return ListView.builder(
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    itemCount: history.length,
                    itemBuilder: (context, index) => _buildPurchaseCard(
                      context,
                      history[index],
                      onTap: () => _showPurchaseDetails(context, history[index]),
                    ),
                  );
                },
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, st) => Center(
                  child: Text('Error: $e'),
                ),
              ),

              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }

  /// Summary cards
  Widget _buildSummaryCards(
    BuildContext context,
    AsyncValue<double> balanceAsync,
    AsyncValue<double> spentAsync,
    AsyncValue<double> avgPriceAsync,
    int purchaseCount,
  ) {
    return Column(
      children: [
        // Total balance & value
        Row(
          children: [
            Expanded(
              child: _buildSummaryCard(
                'Total ANM',
                balanceAsync.maybeWhen(
                  data: (balance) => '${balance.toStringAsFixed(2)}',
                  orElse: () => '--',
                ),
                Icons.account_balance_wallet,
                Colors.blue,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _buildSummaryCard(
                'Total Spent',
                spentAsync.maybeWhen(
                  data: (spent) => '\$${spent.toStringAsFixed(2)}',
                  orElse: () => '--',
                ),
                Icons.attach_money,
                Colors.green,
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),

        // Avg price & purchase count
        Row(
          children: [
            Expanded(
              child: _buildSummaryCard(
                'Avg Price',
                avgPriceAsync.maybeWhen(
                  data: (price) => '\$${price.toStringAsFixed(2)}',
                  orElse: () => '--',
                ),
                Icons.trending_down,
                Colors.orange,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _buildSummaryCard(
                'Purchases',
                purchaseCount.toString(),
                Icons.shopping_cart,
                Colors.purple,
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildSummaryCard(
    String label,
    String value,
    IconData icon,
    Color color,
  ) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        border: Border.all(color: color.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                label,
                style: const TextStyle(
                  fontSize: 12,
                  color: Colors.grey,
                  fontWeight: FontWeight.w500,
                ),
              ),
              Icon(icon, size: 16, color: color),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.bold,
              color: color,
            ),
          ),
        ],
      ),
    );
  }

  /// Purchase card
  Widget _buildPurchaseCard(
    BuildContext context,
    HistoricalPurchase purchase, {
    required VoidCallback onTap,
  }) {
    final dateFormat = DateFormat('MMM d, yyyy • hh:mm a');
    final statusColor = purchase.status == 'completed'
        ? Colors.green
        : purchase.status == 'pending'
            ? Colors.orange
            : Colors.red;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header: amount & status
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${purchase.anmQuantity.toStringAsFixed(2)} ANM',
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      Text(
                        dateFormat.format(purchase.timestamp),
                        style: const TextStyle(
                          fontSize: 12,
                          color: Colors.grey,
                        ),
                      ),
                    ],
                  ),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        '\$${purchase.usdAmount.toStringAsFixed(2)}',
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: statusColor.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          purchase.status[0].toUpperCase() +
                              purchase.status.substring(1),
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            color: statusColor,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),

              const SizedBox(height: 12),
              const Divider(height: 1),
              const SizedBox(height: 12),

              // Details row
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  _buildDetailColumn('Price', '\$${purchase.pricePerAnm.toStringAsFixed(4)}'),
                  _buildDetailColumn('Method', purchase.method.displayName),
                  if (purchase.transactionHash != null)
                    _buildDetailColumn(
                      'Tx',
                      purchase.transactionHash!.substring(0, 8) + '...',
                    ),
                ],
              ),

              const SizedBox(height: 12),

              // Action buttons
              Row(
                children: [
                  if (purchase.receiptUrl != null)
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () {
                          // TODO: Open receipt
                        },
                        icon: const Icon(Icons.receipt, size: 16),
                        label: const Text('Receipt'),
                      ),
                    ),
                  if (purchase.transactionHash != null) const SizedBox(width: 8),
                  if (purchase.transactionHash != null)
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () {
                          // TODO: Open explorer
                        },
                        icon: const Icon(Icons.open_in_new, size: 16),
                        label: const Text('Explorer'),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildDetailColumn(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 11,
            color: Colors.grey,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          value,
          style: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }

  /// Purchase details modal
  void _showPurchaseDetails(BuildContext context, HistoricalPurchase purchase) {
    showModalBottomSheet(
      context: context,
      builder: (context) => Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              'Purchase Details',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 24),

            _buildDetailRow('Order ID', purchase.id),
            _buildDetailRow('Timestamp', DateFormat('MMM d, yyyy hh:mm:ss a').format(purchase.timestamp)),
            _buildDetailRow('ANM Quantity', '${purchase.anmQuantity.toStringAsFixed(8)}'),
            _buildDetailRow('USD Amount', '\$${purchase.usdAmount.toStringAsFixed(2)}'),
            _buildDetailRow('Price per ANM', '\$${purchase.pricePerAnm.toStringAsFixed(8)}'),
            _buildDetailRow('Payment Method', purchase.method.displayName),
            _buildDetailRow('Status', purchase.status),
            if (purchase.transactionHash != null)
              _buildDetailRow('Transaction Hash', purchase.transactionHash!),

            const SizedBox(height: 24),

            // Action buttons
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Close'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton.icon(
                    onPressed: () {
                      // TODO: Export receipt
                    },
                    icon: const Icon(Icons.download),
                    label: const Text('Export'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 12,
              color: Colors.grey,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
