/// Pool configuration page
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/app_state.dart';
import '../../models/miner_config.dart';

class PoolsPage extends ConsumerStatefulWidget {
  const PoolsPage({super.key});

  @override
  ConsumerState<PoolsPage> createState() => _PoolsPageState();
}

class _PoolsPageState extends ConsumerState<PoolsPage> {
  late TextEditingController _urlController;
  late TextEditingController _usernameController;
  bool _enablePool = false;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController();
    _usernameController = TextEditingController();
  }

  @override
  void dispose() {
    _urlController.dispose();
    _usernameController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final config = ref.watch(configProvider);
    
    // Update controllers when config changes
    if (config.pool != null) {
      _urlController.text = config.pool!.url;
      _usernameController.text = config.pool!.username;
      _enablePool = true;
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pool Settings'),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'Pool Mining',
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                        ),
                        Switch(
                          value: _enablePool,
                          onChanged: (value) {
                            setState(() {
                              _enablePool = value;
                              if (!value) {
                                _savePoolConfig(null);
                              }
                            });
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      _enablePool 
                          ? 'Pool mining enabled - shares will be submitted to the configured pool'
                          : 'Solo mining enabled - blocks will be mined independently',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            
            if (_enablePool) ...[
              const SizedBox(height: 16),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Pool Configuration',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 16),
                      
                      TextField(
                        controller: _urlController,
                        decoration: const InputDecoration(
                          labelText: 'Pool URL',
                          hintText: 'stratum+tcp://pool.example.com:3333',
                          prefixIcon: Icon(Icons.link),
                          border: OutlineInputBorder(),
                        ),
                      ),
                      const SizedBox(height: 16),
                      
                      TextField(
                        controller: _usernameController,
                        decoration: const InputDecoration(
                          labelText: 'Username / Wallet Address',
                          hintText: 'anim1q...',
                          prefixIcon: Icon(Icons.person),
                          border: OutlineInputBorder(),
                        ),
                      ),
                      const SizedBox(height: 24),
                      
                      SizedBox(
                        width: double.infinity,
                        child: ElevatedButton.icon(
                          onPressed: _savePoolSettings,
                          icon: const Icon(Icons.save),
                          label: const Text('Save Pool Settings'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              
              const SizedBox(height: 16),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Popular Pools',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 8),
                      _buildPoolOption(
                        'Official Animica Pool',
                        'stratum+tcp://pool.animica.org:3333',
                      ),
                      _buildPoolOption(
                        'Community Pool 1',
                        'stratum+tcp://pool1.animica.community:3333',
                      ),
                      _buildPoolOption(
                        'Community Pool 2',
                        'stratum+tcp://pool2.animica.community:3333',
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildPoolOption(String name, String url) {
    return ListTile(
      title: Text(name),
      subtitle: Text(url, style: const TextStyle(fontSize: 12)),
      trailing: IconButton(
        icon: const Icon(Icons.add_circle_outline),
        onPressed: () {
          setState(() {
            _urlController.text = url;
          });
        },
      ),
    );
  }

  void _savePoolSettings() {
    if (_urlController.text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please enter a pool URL')),
      );
      return;
    }

    final pool = PoolConfig(
      url: _urlController.text,
      username: _usernameController.text,
    );

    _savePoolConfig(pool);

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Pool settings saved')),
    );
  }

  void _savePoolConfig(PoolConfig? pool) {
    ref.read(configProvider.notifier).updatePoolConfig(pool);
  }
}
