// Animica Wallet — App shell
// MaterialApp + themes + router bootstrap

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'l10n/app_localizations.dart';

import 'theme/themes.dart';          // buildLightTheme(), buildDarkTheme()
import 'router.dart';                // createRouter(Env env, {String flavor})
import 'services/env.dart';          // class Env

/// Root widget wiring localization, theming, and routing.
/// `Env` carries runtime config (RPC URLs, chainId, feature flags).
class AnimicaApp extends ConsumerWidget {
  final Env env;
  final String flavor;

  const AnimicaApp({super.key, required this.env, required this.flavor});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // TODO(alex): When state/app_state.dart exists, replace ThemeMode.system
    // with a provider-backed value (e.g., themeModeProvider).
    final ThemeMode themeMode = ThemeMode.system;

    final router = createRouter(env, flavor: flavor);

    return MaterialApp.router(
      debugShowCheckedModeBanner: false,
      // Localization
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      // Titles
      onGenerateTitle: (ctx) => AppLocalizations.of(ctx).appTitle,
      // Themes
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      themeMode: themeMode,
      // Routing
      routerConfig: router,
      // iOS-like page transitions on iOS, default elsewhere
      builder: (context, child) {
        // Set a consistent background & safe area padding for edge-to-edge.
        return ColoredBox(
          color: Theme.of(context).colorScheme.background,
          child: SafeArea(
            top: false, bottom: false, left: false, right: false,
            child: child ?? const SizedBox.shrink(),
          ),
        );
      },
    );
  }
}
