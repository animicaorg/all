import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'router.dart';
import 'screens/unlock.dart';
import 'state/auth_state.dart';
import 'theme.dart';

void main() {
  runApp(const ProviderScope(child: AnimicaWalletApp()));
}

class AnimicaWalletApp extends StatelessWidget {
  const AnimicaWalletApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Animica Wallet',
      theme: AnimicaTheme.light(),
      darkTheme: AnimicaTheme.dark(),
      debugShowCheckedModeBanner: false,
      home: const _AuthGate(),
    );
  }
}

/// Renders the lock/setup screen until the user has unlocked, then
/// hands off to the router-driven main app shell.
class _AuthGate extends ConsumerWidget {
  const _AuthGate();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider);
    return auth.when(
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (e, _) => Scaffold(body: Center(child: Text('Auth error: $e'))),
      data: (s) => s.unlocked
          ? MaterialApp.router(
              title: 'Animica Wallet',
              theme: AnimicaTheme.light(),
              darkTheme: AnimicaTheme.dark(),
              routerConfig: router,
              debugShowCheckedModeBanner: false,
            )
          : const UnlockScreen(),
    );
  }
}
