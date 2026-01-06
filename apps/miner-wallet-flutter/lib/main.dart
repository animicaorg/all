import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:logging/logging.dart';

import 'router/app_router.dart';
import 'theme/app_theme.dart';
import 'utils/logger.dart';

void main() {
  // Initialize logger
  setupLogger();
  final log = Logger('Main');
  
  log.info('Starting Animica Miner-Wallet');

  runApp(
    const ProviderScope(
      child: MinerWalletApp(),
    ),
  );
}

class MinerWalletApp extends ConsumerWidget {
  const MinerWalletApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    
    return MaterialApp.router(
      title: 'Animica Miner-Wallet',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: ThemeMode.dark, // Default to dark theme
      routerConfig: router,
    );
  }
}
