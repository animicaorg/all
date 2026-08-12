import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'router.dart';
import 'screens/auth_error.dart';
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
      error: (e, _) => Scaffold(body: AuthErrorContent(e)),
      data: (s) => s.unlocked
          ? const _UnlockedApp()
          : const UnlockScreen(),
    );
  }
}

class _UnlockedApp extends StatelessWidget {
  const _UnlockedApp();

  @override
  Widget build(BuildContext context) {
    return const _DeepLinkHandler(
      child: _RouterApp(),
    );
  }
}

class _RouterApp extends StatelessWidget {
  const _RouterApp();

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Animica Wallet',
      theme: AnimicaTheme.light(),
      darkTheme: AnimicaTheme.dark(),
      routerConfig: router,
      debugShowCheckedModeBanner: false,
    );
  }
}

/// Routes incoming `animica://` links into the app.
///
/// Today the only one that matters is `animica://wc?...` — the site-pairing
/// URI that animica.xyz renders as a QR code and, on a phone, offers as an
/// "Open in Animica app" button. Handled here rather than in the router so a
/// link that arrives while the wallet is still locked is simply dropped
/// instead of jumping past the unlock screen.
class _DeepLinkHandler extends StatefulWidget {
  final Widget child;
  const _DeepLinkHandler({required this.child});

  @override
  State<_DeepLinkHandler> createState() => _DeepLinkHandlerState();
}

class _DeepLinkHandlerState extends State<_DeepLinkHandler> {
  StreamSubscription<Uri>? _sub;

  @override
  void initState() {
    super.initState();
    _listen();
  }

  Future<void> _listen() async {
    final links = AppLinks();
    // Cold start: the link that launched the app.
    final initial = await links.getInitialLink();
    if (initial != null) _route(initial);
    // Warm start: links delivered while already running.
    _sub = links.uriLinkStream.listen(_route, onError: (_) {});
  }

  void _route(Uri uri) {
    if (uri.scheme != 'animica') return;
    if (uri.host != 'wc' && uri.path != 'wc') return;
    // Hand the whole URI to the connect screen; it does the parsing and
    // validation so the rules live in exactly one place.
    router.go('/connect?uri=${Uri.encodeQueryComponent(uri.toString())}');
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
