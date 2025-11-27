import 'package:flutter/material.dart';

import '../common/placeholder_page.dart';

class OnboardingSuccessPage extends StatelessWidget {
  const OnboardingSuccessPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderPage(title: 'Wallet Ready', icon: Icons.check_circle_outline);
  }
}
