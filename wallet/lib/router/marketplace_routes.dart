/// Marketplace routes for ANM token purchasing
/// 
/// Routes:
/// - /marketplace — Main hub/dashboard
/// - /marketplace/buy — Purchase flow
/// - /marketplace/history — Transaction history
/// - /marketplace/treasury — Treasury dashboard
/// - /marketplace/analytics — Analytics & insights

import 'package:go_router/go_router.dart';
import 'package:flutter/material.dart';

// Import pages
import '../pages/marketplace/marketplace_home_page.dart';
import '../pages/marketplace/buy_anm_page.dart';
import '../pages/marketplace/purchase_history_page.dart';
import '../pages/marketplace/treasury_dashboard_page.dart';

/// Marketplace route definitions
final List<RouteBase> marketplaceRoutes = [
  GoRoute(
    path: '/marketplace',
    name: 'marketplace_home',
    pageBuilder: (context, state) => NoTransitionPage<void>(
      child: const MarketplaceHomePage(),
    ),
    routes: [
      GoRoute(
        path: 'buy',
        name: 'buy_anm',
        pageBuilder: (context, state) => CustomTransitionPage<void>(
          child: const BuyAnmPage(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return SlideTransition(
              position: animation.drive(
                Tween<Offset>(begin: const Offset(1, 0), end: Offset.zero)
                    .chain(CurveTween(curve: Curves.easeInOut)),
              ),
              child: child,
            );
          },
        ),
      ),
      GoRoute(
        path: 'history',
        name: 'purchase_history',
        pageBuilder: (context, state) => CustomTransitionPage<void>(
          child: const PurchaseHistoryPage(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return SlideTransition(
              position: animation.drive(
                Tween<Offset>(begin: const Offset(1, 0), end: Offset.zero)
                    .chain(CurveTween(curve: Curves.easeInOut)),
              ),
              child: child,
            );
          },
        ),
      ),
      GoRoute(
        path: 'treasury',
        name: 'treasury_dashboard',
        pageBuilder: (context, state) => CustomTransitionPage<void>(
          child: const TreasuryDashboardPage(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return SlideTransition(
              position: animation.drive(
                Tween<Offset>(begin: const Offset(1, 0), end: Offset.zero)
                    .chain(CurveTween(curve: Curves.easeInOut)),
              ),
              child: child,
            );
          },
        ),
      ),
      GoRoute(
        path: 'analytics',
        name: 'marketplace_analytics',
        pageBuilder: (context, state) => CustomTransitionPage<void>(
          child: const AnalyticsPage(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return SlideTransition(
              position: animation.drive(
                Tween<Offset>(begin: const Offset(1, 0), end: Offset.zero)
                    .chain(CurveTween(curve: Curves.easeInOut)),
              ),
              child: child,
            );
          },
        ),
      ),
    ],
  ),
];

/// Extension on BuildContext to add marketplace navigation helpers
extension MarketplaceNavigation on BuildContext {
  /// Navigate to marketplace home
  void goToMarketplace() => pushNamed('marketplace_home');

  /// Navigate to buy page
  void goToBuyANM() => pushNamed('buy_anm');

  /// Navigate to purchase history
  void goToPurchaseHistory() => pushNamed('purchase_history');

  /// Navigate to treasury dashboard
  void goToTreasuryDashboard() => pushNamed('treasury_dashboard');

  /// Navigate to analytics
  void goToAnalytics() => pushNamed('marketplace_analytics');
}

/// Analytics page placeholder
/// TODO: Implement full analytics dashboard with charts and metrics
class AnalyticsPage extends StatefulWidget {
  const AnalyticsPage({Key? key}) : super(key: key);

  @override
  State<AnalyticsPage> createState() => _AnalyticsPageState();
}

class _AnalyticsPageState extends State<AnalyticsPage> {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Analytics'),
        elevation: 0,
        backgroundColor: const Color(0xFF5EEAD4),
        foregroundColor: Colors.white,
      ),
      body: CustomScrollView(
        slivers: [
          // Pricing Curve Section
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Pricing Curve Projection',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 16),
                  Container(
                    height: 300,
                    decoration: BoxDecoration(
                      border: Border.all(color: Colors.grey[300]!),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: const [
                          Icon(Icons.show_chart, size: 48, color: Color(0xFF5EEAD4)),
                          SizedBox(height: 16),
                          Text('Price History Chart'),
                          Text(
                            'Historical pricing curve based on treasury depletion',
                            style: TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'Key Insights',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 12),
                  _buildInsightCard(
                    title: 'Price Elasticity',
                    value: '2.0x multiplier at 100% sold',
                    icon: Icons.trending_up,
                  ),
                  _buildInsightCard(
                    title: 'Supply Depletion Rate',
                    value: '~8.2% per month at current rate',
                    icon: Icons.speed,
                  ),
                  _buildInsightCard(
                    title: 'Revenue Acceleration',
                    value: 'Quadratic growth curve',
                    icon: Icons.bar_chart,
                  ),
                ],
              ),
            ),
          ),
          // Buy Volume Section
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Purchase Volume Trends',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 16),
                  Container(
                    height: 250,
                    decoration: BoxDecoration(
                      border: Border.all(color: Colors.grey[300]!),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: const [
                          Icon(Icons.shopping_cart, size: 48, color: Color(0xFF5EEAD4)),
                          SizedBox(height: 16),
                          Text('Volume Analysis Chart'),
                          Text(
                            'ANM purchased over time',
                            style: TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'Volume Metrics',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 12),
                  _buildMetricRow(
                    label: '24h Volume',
                    value: 'Loading...',
                    change: '+12.5%',
                  ),
                  _buildMetricRow(
                    label: '7d Volume',
                    value: 'Loading...',
                    change: '+18.3%',
                  ),
                  _buildMetricRow(
                    label: '30d Volume',
                    value: 'Loading...',
                    change: '+45.7%',
                  ),
                ],
              ),
            ),
          ),
          // Market Analysis Section
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Market Concentration',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 16),
                  Container(
                    height: 250,
                    decoration: BoxDecoration(
                      border: Border.all(color: Colors.grey[300]!),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: const [
                          Icon(Icons.pie_chart, size: 48, color: Color(0xFF5EEAD4)),
                          SizedBox(height: 16),
                          Text('Distribution Chart'),
                          Text(
                            'Payment method distribution',
                            style: TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'Payment Method Breakdown',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 12),
                  _buildPaymentMethodRow('Credit Card', '48%'),
                  _buildPaymentMethodRow('PayPal', '25%'),
                  _buildPaymentMethodRow('Bank Transfer', '15%'),
                  _buildPaymentMethodRow('Crypto', '12%'),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildInsightCard({
    required String title,
    required String value,
    required IconData icon,
  }) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF5EEAD4).withOpacity(0.05),
        border: Border.all(
          color: const Color(0xFF5EEAD4).withOpacity(0.2),
        ),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(icon, color: const Color(0xFF5EEAD4)),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                Text(
                  value,
                  style: TextStyle(color: Colors.grey[600], fontSize: 14),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMetricRow({
    required String label,
    required String value,
    required String change,
  }) {
    final isPositive = !change.startsWith('-');
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(color: Colors.grey[200]!),
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
              Text(value, style: TextStyle(color: Colors.grey[600])),
            ],
          ),
          Text(
            change,
            style: TextStyle(
              color: isPositive ? Colors.green : Colors.red,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPaymentMethodRow(String method, String percentage) {
    final percent = int.tryParse(percentage.replaceAll('%', '')) ?? 0;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(method),
              Text(percentage, style: const TextStyle(fontWeight: FontWeight.w600)),
            ],
          ),
          const SizedBox(height: 4),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: percent / 100,
              minHeight: 8,
              backgroundColor: Colors.grey[200],
              valueColor: AlwaysStoppedAnimation<Color>(
                const Color(0xFF5EEAD4).withOpacity(0.7),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
