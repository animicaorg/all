import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/app_state.dart';

class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final config = ref.watch(configProvider);
    final uiConfig = config.ui;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
      ),
      body: ListView(
        children: [
          const Padding(
            padding: EdgeInsets.all(16.0),
            child: Text(
              'Wallet',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: Colors.grey,
              ),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.account_balance_wallet),
            title: const Text('Wallet Setup'),
            subtitle: const Text('Import or create a wallet'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/wallet-setup'),
          ),
          const Divider(),
          const Padding(
            padding: EdgeInsets.all(16.0),
            child: Text(
              'Mining',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: Colors.grey,
              ),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.devices),
            title: const Text('Devices'),
            subtitle: const Text('Configure CPU and GPU devices'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/devices'),
          ),
          ListTile(
            leading: const Icon(Icons.pool),
            title: const Text('Pool Settings'),
            subtitle: const Text('Mining pool configuration'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/pools'),
          ),
          ListTile(
            leading: const Icon(Icons.article),
            title: const Text('View Logs'),
            subtitle: const Text('Mining logs and debug info'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/logs'),
          ),
          ListTile(
            leading: const Icon(Icons.bar_chart),
            title: const Text('Statistics'),
            subtitle: const Text('Mining stats and charts'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/stats'),
          ),
          ListTile(
            leading: const Icon(Icons.code),
            title: const Text('JSON Configuration'),
            subtitle: const Text('Advanced: Edit raw config'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go('/config'),
          ),
          const Divider(),
          const Padding(
            padding: EdgeInsets.all(16.0),
            child: Text(
              'App Settings',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: Colors.grey,
              ),
            ),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.system_update_alt),
            title: const Text('System Tray'),
            subtitle: const Text('Minimize to system tray'),
            value: uiConfig.systemTray,
            onChanged: (value) {
              ref.read(configProvider.notifier).updateUiConfig(
                uiConfig.copyWith(systemTray: value),
              );
            },
          ),
          SwitchListTile(
            secondary: const Icon(Icons.notifications),
            title: const Text('Notifications'),
            subtitle: const Text('Block found, errors, etc.'),
            value: uiConfig.notifications,
            onChanged: (value) {
              ref.read(configProvider.notifier).updateUiConfig(
                uiConfig.copyWith(notifications: value),
              );
            },
          ),
          const Divider(),
          const Padding(
            padding: EdgeInsets.all(16.0),
            child: Text(
              'About',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: Colors.grey,
              ),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.info_outline),
            title: const Text('About'),
            subtitle: const Text('Version 0.1.0+1'),
            onTap: () {
              showAboutDialog(
                context: context,
                applicationName: 'Animica Miner-Wallet',
                applicationVersion: '0.1.0+1',
                applicationLegalese: '© 2024 Animica',
              );
            },
          ),
        ],
      ),
    );
  }
}
