import 'package:flutter/material.dart';

import '../common/placeholder_page.dart';

class DevToolsPage extends StatelessWidget {
  const DevToolsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const PlaceholderPage(title: 'Developer Tools', icon: Icons.build_circle_outlined);
  }
}
