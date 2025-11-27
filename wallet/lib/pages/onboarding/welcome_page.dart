import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../router.dart';

class WelcomePage extends StatelessWidget {
  const WelcomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Welcome to Animica Wallet')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Get started', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 12),
            Text(
              'Create a new wallet or import an existing recovery phrase.',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: () => context.go(Routes.onboardingCreate),
              icon: const Icon(Icons.add_circle_outline),
              label: const Text('Create new wallet'),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () => context.go(Routes.onboardingImport),
              icon: const Icon(Icons.download_outlined),
              label: const Text('Import existing wallet'),
            ),
          ],
        ),
      ),
    );
  }
}
