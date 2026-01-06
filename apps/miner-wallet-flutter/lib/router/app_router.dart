import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../pages/mining/dashboard_page.dart';
import '../pages/mining/devices_page.dart';
import '../pages/mining/pools_page.dart';
import '../pages/mining/logs_page.dart';
import '../pages/mining/stats_page.dart';
import '../pages/wallet/wallet_page.dart';
import '../pages/wallet/receive_page.dart';
import '../pages/wallet/wallet_setup_page.dart';
import '../pages/wallet/transaction_history_page.dart';
import '../pages/settings/settings_page.dart';
import '../pages/settings/config_page.dart';
import '../pages/onboarding/wizard_page.dart';

final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/dashboard',
    routes: [
      // Wizard route (standalone, not in shell)
      GoRoute(
        path: '/wizard',
        name: 'wizard',
        builder: (context, state) => const WizardPage(),
      ),
      
      // Main shell routes
      ShellRoute(
        builder: (context, state, child) {
          return MainScaffold(child: child);
        },
        routes: [
          GoRoute(
            path: '/dashboard',
            name: 'dashboard',
            pageBuilder: (context, state) => NoTransitionPage(
              child: DashboardPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/devices',
            name: 'devices',
            pageBuilder: (context, state) => NoTransitionPage(
              child: DevicesPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/pools',
            name: 'pools',
            pageBuilder: (context, state) => NoTransitionPage(
              child: PoolsPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/logs',
            name: 'logs',
            pageBuilder: (context, state) => NoTransitionPage(
              child: LogsPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/stats',
            name: 'stats',
            pageBuilder: (context, state) => NoTransitionPage(
              child: StatsPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/wallet',
            name: 'wallet',
            pageBuilder: (context, state) => NoTransitionPage(
              child: WalletPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/receive',
            name: 'receive',
            pageBuilder: (context, state) => NoTransitionPage(
              child: ReceivePage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/wallet-setup',
            name: 'wallet-setup',
            pageBuilder: (context, state) => NoTransitionPage(
              child: WalletSetupPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/transaction-history',
            name: 'transaction-history',
            pageBuilder: (context, state) => NoTransitionPage(
              child: TransactionHistoryPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/settings',
            name: 'settings',
            pageBuilder: (context, state) => NoTransitionPage(
              child: SettingsPage(key: state.pageKey),
            ),
          ),
          GoRoute(
            path: '/config',
            name: 'config',
            pageBuilder: (context, state) => NoTransitionPage(
              child: ConfigPage(key: state.pageKey),
            ),
          ),
        ],
      ),
    ],
  );
});

class MainScaffold extends StatefulWidget {
  final Widget child;

  const MainScaffold({required this.child, super.key});

  @override
  State<MainScaffold> createState() => _MainScaffoldState();
}

class _MainScaffoldState extends State<MainScaffold> {
  int _selectedIndex = 0;

  void _onDestinationSelected(int index) {
    setState(() {
      _selectedIndex = index;
    });

    switch (index) {
      case 0:
        context.go('/dashboard');
      case 1:
        context.go('/wallet');
      case 2:
        context.go('/settings');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _selectedIndex,
            onDestinationSelected: _onDestinationSelected,
            labelType: NavigationRailLabelType.all,
            destinations: const [
              NavigationRailDestination(
                icon: Icon(Icons.dashboard_outlined),
                selectedIcon: Icon(Icons.dashboard),
                label: Text('Mining'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.account_balance_wallet_outlined),
                selectedIcon: Icon(Icons.account_balance_wallet),
                label: Text('Wallet'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.settings_outlined),
                selectedIcon: Icon(Icons.settings),
                label: Text('Settings'),
              ),
            ],
          ),
          const VerticalDivider(thickness: 1, width: 1),
          Expanded(child: widget.child),
        ],
      ),
    );
  }
}
