// GoRouter routes & basic guards (locked/unlocked)
// -------------------------------------------------
// NOTE: This router is wired to be *guard-ready* but uses inert
// defaults until the state providers are implemented. See the
// TODOs to connect real lock/onboarding state from Riverpod.

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

// Planned imports (will exist as you continue the repo scaffolding)
import 'services/env.dart'; // class Env
// Pages (to be added in upcoming steps)
import 'pages/home/home_page.dart';
import 'pages/send/send_page.dart';
import 'pages/receive/receive_page.dart';
import 'pages/contracts/contracts_page.dart';
import 'pages/settings/settings_page.dart';
import 'pages/settings/security_page.dart';
import 'pages/dev/dev_tools_page.dart';
import 'pages/onboarding/welcome_page.dart';
import 'pages/onboarding/create_mnemonic_page.dart';
import 'pages/onboarding/verify_mnemonic_page.dart';
import 'pages/onboarding/import_wallet_page.dart';
import 'pages/onboarding/set_pin_page.dart';
import 'pages/onboarding/success_page.dart';
import 'keyring/keyring.dart';
import 'router/marketplace_routes.dart';

/// Route names (centralized to avoid typos)
abstract class Routes {
  static const String onboardingRoot = '/onboarding';
  static const String onboardingWelcome = '/onboarding/welcome';
  static const String onboardingCreate = '/onboarding/create';
  static const String onboardingVerify = '/onboarding/verify';
  static const String onboardingImport = '/onboarding/import';
  static const String onboardingSetPin = '/onboarding/set-pin';
  static const String onboardingSuccess = '/onboarding/success';

  static const String home = '/';
  static const String send = '/send';
  static const String receive = '/receive';
  static const String contracts = '/contracts';
  static const String settings = '/settings';
  static const String security = '/settings/security';

  static const String devTools = '/dev';
}

/// Global keys (useful for dialogs/snackbars outside BuildContext)
final _rootNavigatorKey = GlobalKey<NavigatorState>();
final _shellNavigatorKey = GlobalKey<NavigatorState>();

// ---------------------------------------------------------------------------
// Guards (stubbed)
// ---------------------------------------------------------------------------
// These booleans are placeholders. Wire them to Riverpod providers shortly.
// For example, define in `state/account_state.dart`:
//   final hasWalletProvider = StateProvider<bool>((_) => false);
//   final isUnlockedProvider = StateProvider<bool>((_) => false);
//
// Then replace the `_GuardState` class with a RouterNotifier that listens to
// those providers and calls `notifyListeners()`; pass it as `refreshListenable`
// and implement redirect logic based on the provider values.
class _GuardState {
  Future<bool> hasWallet() async {
    try {
      return await keyring.hasWallet();
    } catch (e, st) {
      debugPrint('keyring.hasWallet failed, assuming no wallet: $e\n$st');
      return false; // Safe fallback so routing can proceed instead of stalling.
    }
  }

  /// Returns true when the keyring indicates an unlocked wallet. This reads
  /// the synchronous `keyring.status` which is safe as a default guard.
  bool get isUnlocked {
    try {
      return keyring.status == KeyringStatus.unlocked;
    } catch (_) {
      // If keyring isn't available for some reason, assume unlocked so we
      // don't unnecessarily block navigation in early boot scenarios.
      return true;
    }
  }
}

Future<String?> _guardRedirect(_GuardState g, GoRouterState state) async {
  final loc = state.matchedLocation;
  final inOnboarding = loc.startsWith(Routes.onboardingRoot);
  final inSecurity = loc == Routes.security;

  final hasWallet = await g.hasWallet();

  // If no wallet created/imported, force onboarding except when already there.
  if (!hasWallet && !inOnboarding) {
    return Routes.onboardingWelcome;
  }

  // If a wallet already exists, skip the welcome screen.
  if (hasWallet && inOnboarding && loc == Routes.onboardingWelcome) {
    return Routes.home;
  }

  // If wallet exists but is locked, send to security settings (PIN/biometrics)
  if (hasWallet && !g.isUnlocked && !inSecurity && !inOnboarding) {
    return Routes.security;
  }

  return null; // allow navigation
}

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------
GoRouter createRouter(Env env, {String flavor = 'dev'}) {
  final guard = _GuardState();

  return GoRouter(
    navigatorKey: _rootNavigatorKey,
    debugLogDiagnostics: kDebugMode,
    initialLocation: Routes.home,
    redirect: (context, state) => _guardRedirect(guard, state),
    routes: <RouteBase>[
      // Onboarding flow
      GoRoute(
        path: Routes.onboardingWelcome,
        name: 'onboarding_welcome',
        builder: (ctx, st) => const WelcomePage(),
        routes: [
          GoRoute(
            path: 'create',
            name: 'onboarding_create',
            builder: (ctx, st) => const CreateMnemonicPage(),
          ),
          GoRoute(
            path: 'verify',
            name: 'onboarding_verify',
            builder: (ctx, st) => const VerifyMnemonicPage(),
          ),
          GoRoute(
            path: 'import',
            name: 'onboarding_import',
            builder: (ctx, st) => const ImportWalletPage(),
          ),
          GoRoute(
            path: 'set-pin',
            name: 'onboarding_set_pin',
            builder: (ctx, st) => const SetPinPage(),
          ),
          GoRoute(
            path: 'success',
            name: 'onboarding_success',
            builder: (ctx, st) => const OnboardingSuccessPage(),
          ),
        ],
      ),

      // App shell (single navigator; you can upgrade to StatefulShellRoute later)
      ShellRoute(
        navigatorKey: _shellNavigatorKey,
        builder: (context, state, child) {
          // If you plan a bottom nav, wrap `child` with your Scaffold here.
          return child;
        },
        routes: [
          GoRoute(
            path: Routes.home,
            name: 'home',
            builder: (ctx, st) => const HomePage(),
          ),
          GoRoute(
            path: Routes.send,
            name: 'send',
            builder: (ctx, st) => const SendPage(),
          ),
          GoRoute(
            path: Routes.receive,
            name: 'receive',
            builder: (ctx, st) => const ReceivePage(),
          ),
          GoRoute(
            path: Routes.contracts,
            name: 'contracts',
            builder: (ctx, st) => const ContractsPage(),
          ),
          GoRoute(
            path: Routes.settings,
            name: 'settings',
            builder: (ctx, st) => const SettingsPage(),
            routes: [
              GoRoute(
                path: 'security',
                name: 'security',
                builder: (ctx, st) => const SecurityPage(),
              ),
            ],
          ),
          if (flavor != 'prod')
            GoRoute(
              path: Routes.devTools,
              name: 'dev_tools',
              builder: (ctx, st) => const DevToolsPage(),
            ),
          // Marketplace routes
          ...marketplaceRoutes,
        ],
      ),
    ],

    // Fallback error page
    errorBuilder: (context, state) => _RouterErrorPage(error: state.error),
  );
}

// ---------------------------------------------------------------------------
// Minimal error page to avoid crashes on unknown routes
// ---------------------------------------------------------------------------
class _RouterErrorPage extends StatelessWidget {
  final Object? error;
  const _RouterErrorPage({this.error});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Oops')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline, size: 48),
              const SizedBox(height: 12),
              Text(
                'Navigation error',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 8),
              Text(
                '${error ?? 'Unknown'}',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: () => context.go(Routes.home),
                child: const Text('Go Home'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
