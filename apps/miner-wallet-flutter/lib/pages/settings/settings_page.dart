import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
      ),
      body: ListView(
        children: [
          ListTile(
            leading: const Icon(Icons.language),
            title: const Text('Network'),
            subtitle: const Text('RPC URL and Chain ID'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to network settings
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.settings_applications),
            title: const Text('Mining Configuration'),
            subtitle: const Text('Device settings and performance'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to mining config
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.pool),
            title: const Text('Pool Settings'),
            subtitle: const Text('Configure mining pool'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to pool settings
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.code),
            title: const Text('JSON Configuration'),
            subtitle: const Text('Edit raw config'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to JSON editor
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.list_alt),
            title: const Text('Logs'),
            subtitle: const Text('View mining logs'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to logs
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.bar_chart),
            title: const Text('Statistics'),
            subtitle: const Text('Hashrate graphs and stats'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              // TODO: Navigate to stats
            },
          ),
          const Divider(),
          SwitchListTile(
            secondary: const Icon(Icons.app_shortcut),
            title: const Text('System Tray'),
            subtitle: const Text('Minimize to system tray'),
            value: true,
            onChanged: (value) {
              // TODO: Toggle system tray
            },
          ),
          SwitchListTile(
            secondary: const Icon(Icons.notifications),
            title: const Text('Notifications'),
            subtitle: const Text('Show mining notifications'),
            value: true,
            onChanged: (value) {
              // TODO: Toggle notifications
            },
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.info_outline),
            title: const Text('About'),
            subtitle: const Text('Version 0.1.0'),
            onTap: () {
              // TODO: Show about dialog
            },
          ),
        ],
      ),
    );
  }
}
