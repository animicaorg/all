import 'package:flutter/material.dart';

import '../common/placeholder_page.dart';

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderPage(title: 'Home', icon: Icons.home_outlined);
  }
}
