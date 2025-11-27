import 'package:flutter/material.dart';

import '../common/placeholder_page.dart';

class WelcomePage extends StatelessWidget {
  const WelcomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderPage(title: 'Welcome', icon: Icons.celebration_outlined);
  }
}
