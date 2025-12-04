/// Buy ANM Token Page
/// 
/// Complete purchase flow:
/// 1. Display current price & treasury info
/// 2. Input quantity
/// 3. Select payment method
/// 4. Review & confirm
/// 5. Process payment
/// 6. Show receipt

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/providers.dart';
import '../../services/payment_gateway.dart';
import '../../widgets/chart_widget.dart';
import '../../services/pricing_engine.dart' show TreasurySnapshot;

class BuyAnmPage extends ConsumerStatefulWidget {
  const BuyAnmPage({Key? key}) : super(key: key);

  @override
  ConsumerState<BuyAnmPage> createState() => _BuyAnmPageState();
}

class _BuyAnmPageState extends ConsumerState<BuyAnmPage> {
  final _quantityController = TextEditingController();
  int _currentStep = 0; // 0: input, 1: method, 2: review, 3: processing, 4: receipt

  @override
  void dispose() {
    _quantityController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final purchaseState = ref.watch(purchaseStateProvider);
    final isProcessing = purchaseState.isProcessing;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Buy ANM Token'),
        elevation: 0,
      ),
      body: Stack(
        children: [
          SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: switch (_currentStep) {
              0 => _buildInputStep(context),
              1 => _buildMethodSelectionStep(context),
              2 => _buildReviewStep(context),
              3 => _buildProcessingStep(),
              4 => _buildReceiptStep(),
              _ => const SizedBox(),
            },
          ),
          if (isProcessing)
            LoadingOverlay(
              message: 'Processing payment...',
            ),
        ],
      ),
    );
  }

  /// Step 1: Input quantity
  Widget _buildInputStep(BuildContext context) {
    final priceAsync = ref.watch(anmPriceProvider);
    final treasuryAsync = ref.watch(treasurySnapshotProvider);
    final purchaseState = ref.watch(purchaseStateProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Price header
        priceAsync.when(
          data: (price) => _buildPriceHeader(price),
          loading: () => const CircularProgressIndicator(),
          error: (e, st) => Text('Error: $e'),
        ),
        const SizedBox(height: 32),

        // Treasury status
        treasuryAsync.when(
          data: (treasury) => _buildTreasuryStatus(treasury),
          loading: () => const SizedBox(),
          error: (e, st) => const SizedBox(),
        ),
        const SizedBox(height: 32),

        // Quantity input
        Text(
          'Quantity (ANM)',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _quantityController,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: InputDecoration(
            hintText: 'Enter amount',
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
            ),
            suffixText: 'ANM',
          ),
          onChanged: (value) {
            final qty = double.tryParse(value) ?? 0;
            ref.read(purchaseStateProvider.notifier).setQuantity(qty);
          },
        ),
        const SizedBox(height: 16),

        // Price & fee breakdown
        if (purchaseState.anmQuantity > 0)
          priceAsync.when(
            data: (price) => _buildCostBreakdown(price, purchaseState.anmQuantity),
            loading: () => const SizedBox(),
            error: (e, st) => const SizedBox(),
          ),

        const SizedBox(height: 32),

        // Next button
        SizedBox(
          width: double.infinity,
          child: ElevatedButton(
            onPressed: purchaseState.anmQuantity > 0
                ? () => setState(() => _currentStep = 1)
                : null,
            child: const Text('Continue'),
          ),
        ),
      ],
    );
  }

  /// Price header card
  Widget _buildPriceHeader(double price) {
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
            'Current ANM Price',
            style: TextStyle(color: Colors.white70, fontSize: 14),
          ),
          const SizedBox(height: 8),
          Text(
            '\$${price.toStringAsFixed(2)}',
            style: const TextStyle(
              color: Colors.white,
              fontSize: 36,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 12),
          _buildPriceInfo(),
        ],
      ),
    );
  }

  /// Price info & market data
  Widget _buildPriceInfo() {
    final marketAsync = ref.watch(currentMarketPriceProvider);

    return marketAsync.when(
      data: (market) => Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          _buildInfoChip('24h Change', '${market.change24h.toStringAsFixed(2)}%'),
          _buildInfoChip('Market Cap', '\$${(market.marketCap / 1e9).toStringAsFixed(1)}B'),
          _buildInfoChip('Volume', '\$${(market.volume24h / 1e6).toStringAsFixed(1)}M'),
        ],
      ),
      loading: () => const SizedBox(),
      error: (e, st) => const SizedBox(),
    );
  }

  Widget _buildInfoChip(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 11, color: Colors.white70),
        ),
        Text(
          value,
          style: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
            color: Colors.white,
          ),
        ),
      ],
    );
  }

  /// Treasury status section
  Widget _buildTreasuryStatus(TreasurySnapshot treasury) {
    final revenueAsync = ref.watch(treasuryRevenueProvider);
    const targetRevenue = 1e9;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Treasury Status',
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            _buildStatCard(
              'Sold',
              '${treasury.percentSold.toStringAsFixed(1)}%',
              '${(treasury.soldToDate / 1e9).toStringAsFixed(2)}B',
            ),
            _buildStatCard(
              'Remaining',
              '${((100 - treasury.percentSold).toStringAsFixed(1))}%',
              '${(treasury.treasuryBalance / 1e9).toStringAsFixed(2)}B',
            ),
          ],
        ),
        const SizedBox(height: 12),
        revenueAsync.when(
          data: (revenue) {
            final progressPercent = (revenue / targetRevenue * 100).clamp(0, 100);
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      'To \$1B Target',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    Text(
                      '${progressPercent.toStringAsFixed(1)}%',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            fontWeight: FontWeight.bold,
                          ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: progressPercent / 100,
                    minHeight: 8,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  '\$${(revenue / 1e9).toStringAsFixed(2)}B / \$1.00B',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Colors.grey,
                      ),
                ),
              ],
            );
          },
          loading: () => const SizedBox(),
          error: (e, st) => const SizedBox(),
        ),
      ],
    );
  }

  Widget _buildStatCard(String label, String percent, String value) {
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: const TextStyle(fontSize: 12, color: Colors.grey),
              ),
              const SizedBox(height: 4),
              Text(
                percent,
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 4),
              Text(
                value,
                style: const TextStyle(fontSize: 12, color: Colors.grey),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Cost breakdown
  Widget _buildCostBreakdown(double price, double quantity) {
    final subtotal = price * quantity;
    final fee = subtotal * 0.03; // Assume 3% fee
    final total = subtotal + fee;

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.grey[100],
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          _buildBreakdownRow('Subtotal', '\$${subtotal.toStringAsFixed(2)}'),
          const Divider(height: 16),
          _buildBreakdownRow('Est. Fee (3%)', '\$${fee.toStringAsFixed(2)}', isGrey: true),
          const SizedBox(height: 8),
          _buildBreakdownRow('Total', '\$${total.toStringAsFixed(2)}',
              isBold: true),
        ],
      ),
    );
  }

  Widget _buildBreakdownRow(String label, String value, {bool isGrey = false, bool isBold = false}) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: TextStyle(
            fontSize: 14,
            color: isGrey ? Colors.grey : Colors.black,
            fontWeight: isBold ? FontWeight.bold : FontWeight.normal,
          ),
        ),
        Text(
          value,
          style: TextStyle(
            fontSize: 14,
            fontWeight: isBold ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ],
    );
  }

  /// Step 2: Payment method selection
  Widget _buildMethodSelectionStep(BuildContext context) {
    final purchaseState = ref.watch(purchaseStateProvider);
    final availableMethods = PaymentMethod.values;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Select Payment Method',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 24),

        ...availableMethods.map((method) {
          final isSelected = purchaseState.selectedMethod == method;
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Card(
              color: isSelected ? const Color(0xFF5EEAD4) : null,
              child: ListTile(
                onTap: () {
                  ref.read(purchaseStateProvider.notifier).selectPaymentMethod(method);
                },
                leading: _buildPaymentIcon(method),
                title: Text(
                  method.displayName,
                  style: TextStyle(
                    fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                    color: isSelected ? Colors.white : null,
                  ),
                ),
                subtitle: Text(
                  _getPaymentDescription(method),
                  style: TextStyle(
                    color: isSelected ? Colors.white70 : Colors.grey,
                  ),
                ),
                trailing: isSelected
                    ? const Icon(Icons.check, color: Colors.white)
                    : null,
              ),
            ),
          );
        }),

        const SizedBox(height: 32),
        Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () => setState(() => _currentStep = 0),
                child: const Text('Back'),
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: ElevatedButton(
                onPressed: purchaseState.selectedMethod != null
                    ? () => setState(() => _currentStep = 2)
                    : null,
                child: const Text('Continue'),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildPaymentIcon(PaymentMethod method) {
    return switch (method) {
      PaymentMethod.creditCard => const Icon(Icons.credit_card),
      PaymentMethod.applePay => const Icon(Icons.apple),
      PaymentMethod.googlePay => const Icon(Icons.payment),
      PaymentMethod.paypal => const Icon(Icons.account_balance_wallet),
      PaymentMethod.bankTransfer => const Icon(Icons.account_balance),
      PaymentMethod.crypto => const Icon(Icons.currency_bitcoin),
    };
  }

  String _getPaymentDescription(PaymentMethod method) {
    return switch (method) {
      PaymentMethod.creditCard => 'Visa, Mastercard, American Express',
      PaymentMethod.applePay => 'Fast & secure with Face ID',
      PaymentMethod.googlePay => 'One-tap checkout',
      PaymentMethod.paypal => 'PayPal account balance',
      PaymentMethod.bankTransfer => 'Direct bank transfer (ACH)',
      PaymentMethod.crypto => 'Direct ANM token transfer',
    };
  }

  /// Step 3: Review & confirm
  Widget _buildReviewStep(BuildContext context) {
    final purchaseState = ref.watch(purchaseStateProvider);
    final priceAsync = ref.watch(anmPriceProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Review Your Purchase',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 24),

        // Order summary
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Order Summary',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 16),
                _buildSummaryRow('Quantity', '${purchaseState.anmQuantity.toStringAsFixed(2)} ANM'),
                _buildSummaryRow(
                  'Price per Token',
                  priceAsync.maybeWhen(
                    data: (price) => '\$${price.toStringAsFixed(2)}',
                    orElse: () => '--',
                  ),
                ),
                const Divider(height: 16),
                if (priceAsync.hasValue)
                  _buildSummaryRow(
                    'Total',
                    '\$${(purchaseState.anmQuantity * (priceAsync.value ?? 0)).toStringAsFixed(2)}',
                    isBold: true,
                  ),
              ],
            ),
          ),
        ),

        const SizedBox(height: 16),

        // Payment method
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Payment Method',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    _buildPaymentIcon(purchaseState.selectedMethod!),
                    const SizedBox(width: 12),
                    Text(purchaseState.selectedMethod!.displayName),
                  ],
                ),
              ],
            ),
          ),
        ),

        const SizedBox(height: 32),

        // Terms checkbox
        Row(
          children: [
            Checkbox(value: true, onChanged: (_) {}),
            Expanded(
              child: Text(
                'I agree to Terms of Service',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          ],
        ),

        const SizedBox(height: 32),

        // Action buttons
        Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () => setState(() => _currentStep = 1),
                child: const Text('Back'),
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: ElevatedButton(
                onPressed: () {
                  ref
                      .read(purchaseStateProvider.notifier)
                      .createPaymentIntent()
                      .then((_) {
                    setState(() => _currentStep = 3);
                  });
                },
                child: const Text('Complete Purchase'),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildSummaryRow(String label, String value, {bool isBold = false}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            label,
            style: TextStyle(
              color: Colors.grey,
              fontWeight: isBold ? FontWeight.bold : FontWeight.normal,
            ),
          ),
          Text(
            value,
            style: TextStyle(
              fontWeight: isBold ? FontWeight.bold : FontWeight.normal,
            ),
          ),
        ],
      ),
    );
  }

  /// Step 4: Processing
  Widget _buildProcessingStep() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: const [
          CircularProgressIndicator(),
          SizedBox(height: 16),
          Text('Processing your payment...'),
        ],
      ),
    );
  }

  /// Step 5: Receipt
  Widget _buildReceiptStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        const Icon(Icons.check_circle, size: 64, color: Colors.green),
        const SizedBox(height: 16),
        Text(
          'Purchase Complete!',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 8),
        const Text('Your ANM tokens will appear in your wallet shortly.'),
        const SizedBox(height: 32),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: const [
                Text('Order Details'),
                // TODO: Show actual order details
              ],
            ),
          ),
        ),
        const SizedBox(height: 32),
        SizedBox(
          width: double.infinity,
          child: ElevatedButton(
            onPressed: () {
              ref.read(purchaseStateProvider.notifier).reset();
              Navigator.of(context).pop();
            },
            child: const Text('Done'),
          ),
        ),
      ],
    );
  }
}
