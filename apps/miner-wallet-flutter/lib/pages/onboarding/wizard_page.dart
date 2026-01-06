/// First-run wizard page for initial setup
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/app_state.dart';
import '../../state/wallet_state.dart';
import '../../models/miner_config.dart';

class WizardPage extends ConsumerStatefulWidget {
  const WizardPage({super.key});

  @override
  ConsumerState<WizardPage> createState() => _WizardPageState();
}

class _WizardPageState extends ConsumerState<WizardPage> {
  int _currentStep = 0;
  final _formKey = GlobalKey<FormState>();

  // Step 1: Network
  final _rpcUrlController = TextEditingController(text: 'https://rpc.clearblocker.com');
  final _chainIdController = TextEditingController(text: '2');

  // Step 2: Wallet
  final _walletAddressController = TextEditingController();

  // Step 3: Mining
  bool _enableCpu = true;
  int _cpuThreads = 2;

  @override
  void dispose() {
    _rpcUrlController.dispose();
    _chainIdController.dispose();
    _walletAddressController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Setup Wizard'),
      ),
      body: Stepper(
        currentStep: _currentStep,
        onStepContinue: _onStepContinue,
        onStepCancel: _onStepCancel,
        steps: [
          Step(
            title: const Text('Network Configuration'),
            content: _buildNetworkStep(),
            isActive: _currentStep >= 0,
          ),
          Step(
            title: const Text('Wallet Setup'),
            content: _buildWalletStep(),
            isActive: _currentStep >= 1,
          ),
          Step(
            title: const Text('Mining Settings'),
            content: _buildMiningStep(),
            isActive: _currentStep >= 2,
          ),
          Step(
            title: const Text('Complete'),
            content: _buildCompleteStep(),
            isActive: _currentStep >= 3,
          ),
        ],
      ),
    );
  }

  Widget _buildNetworkStep() {
    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Configure your connection to the Animica network',
            style: TextStyle(color: Colors.grey),
          ),
          const SizedBox(height: 16),
          TextFormField(
            controller: _rpcUrlController,
            decoration: const InputDecoration(
              labelText: 'RPC URL',
              hintText: 'https://rpc.clearblocker.com',
              prefixIcon: Icon(Icons.cloud),
              border: OutlineInputBorder(),
            ),
            validator: (value) {
              if (value == null || value.isEmpty) {
                return 'Please enter an RPC URL';
              }
              if (!value.startsWith('http://') && !value.startsWith('https://')) {
                return 'URL must start with http:// or https://';
              }
              return null;
            },
          ),
          const SizedBox(height: 16),
          TextFormField(
            controller: _chainIdController,
            decoration: const InputDecoration(
              labelText: 'Chain ID',
              hintText: '2',
              prefixIcon: Icon(Icons.numbers),
              border: OutlineInputBorder(),
            ),
            keyboardType: TextInputType.number,
            validator: (value) {
              if (value == null || value.isEmpty) {
                return 'Please enter a chain ID';
              }
              if (int.tryParse(value) == null) {
                return 'Chain ID must be a number';
              }
              return null;
            },
          ),
        ],
      ),
    );
  }

  Widget _buildWalletStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Enter your wallet address for mining rewards',
          style: TextStyle(color: Colors.grey),
        ),
        const SizedBox(height: 16),
        TextFormField(
          controller: _walletAddressController,
          decoration: const InputDecoration(
            labelText: 'Payout Address',
            hintText: 'anim1q...',
            prefixIcon: Icon(Icons.account_balance_wallet),
            border: OutlineInputBorder(),
          ),
          validator: (value) {
            if (value == null || value.isEmpty) {
              return 'Please enter a wallet address';
            }
            if (!value.startsWith('anim1')) {
              return 'Invalid Animica address';
            }
            return null;
          },
        ),
        const SizedBox(height: 16),
        Card(
          color: Colors.blue.withOpacity(0.1),
          child: const Padding(
            padding: EdgeInsets.all(16),
            child: Row(
              children: [
                Icon(Icons.info_outline, color: Colors.blue),
                SizedBox(width: 16),
                Expanded(
                  child: Text(
                    'Mining rewards will be sent to this address',
                    style: TextStyle(color: Colors.blue),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildMiningStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Configure your mining hardware',
          style: TextStyle(color: Colors.grey),
        ),
        const SizedBox(height: 16),
        SwitchListTile(
          title: const Text('Enable CPU Mining'),
          subtitle: const Text('Use CPU for mining'),
          value: _enableCpu,
          onChanged: (value) {
            setState(() {
              _enableCpu = value;
            });
          },
        ),
        if (_enableCpu) ...[
          const SizedBox(height: 16),
          Text('CPU Threads: $_cpuThreads'),
          Slider(
            value: _cpuThreads.toDouble(),
            min: 1,
            max: 8,
            divisions: 7,
            label: _cpuThreads.toString(),
            onChanged: (value) {
              setState(() {
                _cpuThreads = value.toInt();
              });
            },
          ),
        ],
      ],
    );
  }

  Widget _buildCompleteStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Setup complete! Review your configuration below:',
          style: TextStyle(color: Colors.grey),
        ),
        const SizedBox(height: 16),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _buildSummaryRow('RPC URL', _rpcUrlController.text),
                _buildSummaryRow('Chain ID', _chainIdController.text),
                _buildSummaryRow('Payout Address', _walletAddressController.text),
                _buildSummaryRow('CPU Mining', _enableCpu ? 'Enabled ($_cpuThreads threads)' : 'Disabled'),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        SizedBox(
          width: double.infinity,
          child: ElevatedButton.icon(
            onPressed: _finishWizard,
            icon: const Icon(Icons.check_circle),
            label: const Text('Finish Setup'),
            style: ElevatedButton.styleFrom(
              padding: const EdgeInsets.all(16),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSummaryRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
          ),
          Expanded(
            child: Text(value),
          ),
        ],
      ),
    );
  }

  void _onStepContinue() {
    if (_currentStep == 0) {
      if (_formKey.currentState?.validate() ?? false) {
        setState(() {
          _currentStep++;
        });
      }
    } else if (_currentStep == 1) {
      if (_walletAddressController.text.isNotEmpty &&
          _walletAddressController.text.startsWith('anim1')) {
        setState(() {
          _currentStep++;
        });
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Please enter a valid wallet address')),
        );
      }
    } else if (_currentStep < 3) {
      setState(() {
        _currentStep++;
      });
    }
  }

  void _onStepCancel() {
    if (_currentStep > 0) {
      setState(() {
        _currentStep--;
      });
    }
  }

  void _finishWizard() {
    // Save configuration
    final config = ref.read(configProvider);
    final updatedConfig = config.copyWith(
      network: NetworkConfig(
        rpcUrl: _rpcUrlController.text,
        chainId: int.parse(_chainIdController.text),
        networkName: 'Animica Network',
      ),
      miner: config.miner.copyWith(
        payoutAddress: _walletAddressController.text,
      ),
      cpu: config.cpu.copyWith(
        enabled: _enableCpu,
        threads: _cpuThreads,
      ),
    );

    ref.read(configProvider.notifier).updateConfig(updatedConfig);

    // Save wallet address
    ref.read(walletServiceProvider).saveAddress(_walletAddressController.text);

    // Navigate to dashboard
    context.go('/dashboard');

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('✓ Setup complete! You can now start mining.'),
        backgroundColor: Colors.green,
      ),
    );
  }
}
