// Lock screen + first-run password setup. Renders whenever the AuthStatus
// indicates the wallet isn't yet unlocked.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/auth_state.dart';
import 'auth_error.dart';

class UnlockScreen extends ConsumerStatefulWidget {
  const UnlockScreen({super.key});
  @override
  ConsumerState<UnlockScreen> createState() => _UnlockScreenState();
}

class _UnlockScreenState extends ConsumerState<UnlockScreen> {
  final _ctrl = TextEditingController();
  final _ctrl2 = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _ctrl.dispose();
    _ctrl2.dispose();
    super.dispose();
  }

  Future<void> _submit(AuthStatus auth) async {
    setState(() {
      _error = null;
      _busy = true;
    });
    try {
      if (auth.configured) {
        // Unlock
        final ok = await ref.read(authProvider.notifier).unlock(_ctrl.text);
        if (!ok) {
          setState(() => _error = 'Wrong password.');
        }
      } else {
        // Setup
        if (_ctrl.text.length < 6) {
          setState(() => _error = 'Password must be at least 6 characters.');
        } else if (_ctrl.text != _ctrl2.text) {
          setState(() => _error = 'Passwords do not match.');
        } else {
          await ref.read(authProvider.notifier).setupPassword(_ctrl.text);
        }
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final authAsync = ref.watch(authProvider);
    return Scaffold(
      body: authAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => AuthErrorContent(e),
        data: (auth) {
          final isSetup = !auth.configured;
          return SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      isSetup ? Icons.security : Icons.lock_outline,
                      size: 56,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      isSetup ? 'Set a wallet password' : 'Unlock wallet',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      isSetup
                          ? 'The password encrypts your secret keys on this device. There is no recovery — write it down.'
                          : 'Enter your password to decrypt your secret keys.',
                      textAlign: TextAlign.center,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.outline),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _ctrl,
                      autofocus: true,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'Password',
                        border: OutlineInputBorder(),
                      ),
                      onSubmitted: (_) {
                        if (!isSetup) _submit(auth);
                      },
                    ),
                    if (isSetup) ...[
                      const SizedBox(height: 12),
                      TextField(
                        controller: _ctrl2,
                        obscureText: true,
                        decoration: const InputDecoration(
                          labelText: 'Confirm password',
                          border: OutlineInputBorder(),
                        ),
                        onSubmitted: (_) => _submit(auth),
                      ),
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Text(_error!,
                          style: TextStyle(color: Theme.of(context).colorScheme.error)),
                    ],
                    const SizedBox(height: 20),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        onPressed: _busy ? null : () => _submit(auth),
                        child: Text(isSetup ? 'Create password' : 'Unlock'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
