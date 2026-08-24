// go_router config — tabbed shell + push routes for send/buy/browser detail.

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'screens/browser.dart';
import 'screens/buy.dart';
import 'screens/connect_site.dart';
import 'screens/dex/dex_home.dart';
import 'screens/dex/launch_token.dart';
import 'screens/dex/liquidity.dart';
import 'screens/dex/promote.dart';
import 'screens/dex/swap.dart';
import 'screens/games.dart';
import 'screens/history.dart';
import 'screens/home.dart';
import 'screens/l2.dart';
import 'screens/nfts.dart';
import 'screens/receive.dart';
import 'screens/send.dart';
import 'screens/serve.dart';
import 'screens/settings.dart';
import 'screens/store/store_app_detail.dart';
import 'screens/store/store_home.dart';
import 'screens/store/store_library.dart';
import 'screens/store/subscriptions_screen.dart';
import 'screens/store/topup_screen.dart';
import 'screens/tokens.dart';
import 'screens/tx_detail.dart';

final router = GoRouter(
  initialLocation: '/',
  routes: [
    StatefulShellRoute.indexedStack(
      builder: (context, state, shell) => _Shell(shell: shell),
      branches: [
        StatefulShellBranch(routes: [
          GoRoute(path: '/', builder: (c, s) => const HomeScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/tokens', builder: (c, s) => const TokensScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/nfts', builder: (c, s) => const NftsScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/browser', builder: (c, s) => const BrowserScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/games', builder: (c, s) => const GamesScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/store', builder: (c, s) => const StoreHomeScreen()),
        ]),
        StatefulShellBranch(routes: [
          GoRoute(path: '/settings', builder: (c, s) => const SettingsScreen()),
        ]),
      ],
    ),
    // Games open in the dapp browser (provider-enabled on whitelisted hosts,
    // so in-game wallet sign-in works) — pushed on TOP of the shell so the
    // player gets a full-screen session with a back affordance.
    GoRoute(
      path: '/games/play',
      builder: (c, s) =>
          BrowserScreen(initialUrl: s.uri.queryParameters['url']),
    ),
    // Serve & Earn is pushed on top of the shell (like /games/play): it is
    // a leave-it-running full-screen session, not a tab you flick past.
    GoRoute(path: '/serve', builder: (c, s) => const ServeScreen()),
    GoRoute(path: '/send', builder: (c, s) => const SendScreen()),
    GoRoute(path: '/l2', builder: (c, s) => const L2Screen()),
    GoRoute(path: '/receive', builder: (c, s) => const ReceiveScreen()),
    GoRoute(path: '/history', builder: (c, s) => const HistoryScreen()),
    GoRoute(
      path: '/history/tx/:hash',
      builder: (c, s) => TxDetailScreen(hash: s.pathParameters['hash']!),
    ),
    // `uri` is set when we arrive from an `animica://wc?...` deep link; when
    // opened from the wallet UI it is absent and the screen shows the scanner.
    GoRoute(
      path: '/connect',
      builder: (c, s) => ConnectSiteScreen(initialUri: s.uri.queryParameters['uri']),
    ),
    GoRoute(path: '/buy', builder: (c, s) => const BuyScreen()),
    // Token launcher + DEX.
    GoRoute(path: '/dex', builder: (c, s) => const DexHomeScreen()),
    GoRoute(path: '/dex/launch', builder: (c, s) => const LaunchTokenScreen()),
    GoRoute(
      path: '/dex/swap',
      builder: (c, s) => SwapScreen(initialToken: s.uri.queryParameters['token']),
    ),
    GoRoute(path: '/dex/liquidity', builder: (c, s) => const LiquidityScreen()),
    GoRoute(
      path: '/dex/promote/:addr',
      builder: (c, s) => PromoteScreen(contract: s.pathParameters['addr']!),
    ),
    GoRoute(
      path: '/dex/token/:addr',
      builder: (c, s) => TokenDetailScreen(address: s.pathParameters['addr']!),
    ),
    GoRoute(
      path: '/store/library',
      builder: (c, s) => const StoreLibraryScreen(),
    ),
    GoRoute(
      path: '/store/subscriptions',
      builder: (c, s) => const SubscriptionsScreen(),
    ),
    GoRoute(
      path: '/store/topup',
      builder: (c, s) => const TopUpScreen(),
    ),
    GoRoute(
      path: '/store/app/:slug',
      builder: (c, s) => StoreAppDetailScreen(slug: s.pathParameters['slug']!),
    ),
  ],
);

class _Shell extends StatelessWidget {
  final StatefulNavigationShell shell;
  const _Shell({required this.shell});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: shell.goBranch,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.account_balance_wallet_outlined), selectedIcon: Icon(Icons.account_balance_wallet), label: 'Wallet'),
          NavigationDestination(icon: Icon(Icons.toll_outlined), selectedIcon: Icon(Icons.toll), label: 'Tokens'),
          NavigationDestination(icon: Icon(Icons.collections_outlined), selectedIcon: Icon(Icons.collections), label: 'NFTs'),
          NavigationDestination(icon: Icon(Icons.travel_explore_outlined), selectedIcon: Icon(Icons.travel_explore), label: 'Browser'),
          NavigationDestination(icon: Icon(Icons.sports_esports_outlined), selectedIcon: Icon(Icons.sports_esports), label: 'Games'),
          NavigationDestination(icon: Icon(Icons.storefront_outlined), selectedIcon: Icon(Icons.storefront), label: 'Store'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
