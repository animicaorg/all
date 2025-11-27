import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../router.dart';

class OnboardingSuccessPage extends StatelessWidget {
  const OnboardingSuccessPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Wallet ready')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.check_circle_outline, size: 64),
            const SizedBox(height: 12),
            Text('All set!', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              'Your wallet has been configured. You can now send and receive funds.',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            const Spacer(),
            FilledButton.icon(
              onPressed: () => context.go(Routes.home),
              icon: const Icon(Icons.arrow_forward),
              label: const Text('Go to wallet'),
            ),
          ],
        ),
      ),
    );
  }
}
