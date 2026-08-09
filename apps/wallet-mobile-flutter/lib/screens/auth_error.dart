// Recovery surface shown when the auth provider fails to initialise.
//
// The important case is WalletStorageCorruptedException: the OS secure-storage
// blob can't be decrypted (typically after an Android backup / device-transfer
// restore onto a phone whose Keystore lacks the original master key). Rather
// than dead-end on "Auth error: <stack>", we explain what happened, reassure
// the user their funds are safe on-chain, and offer a one-tap reset that wipes
// the unusable local data and drops them into first-run onboarding to
// re-import their recovery phrase.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/auth.dart';
import '../state/auth_state.dart';

/// Centered recovery content (no Scaffold — callers wrap it so it composes
/// with both the app-gate and the unlock screen).
class AuthErrorContent extends ConsumerStatefulWidget {
  final Object error;
  const AuthErrorContent(this.error, {super.key});

  @override
  ConsumerState<AuthErrorContent> createState() => _AuthErrorContentState();
}

class _AuthErrorContentState extends ConsumerState<AuthErrorContent> {
  bool _busy = false;

  Future<void> _reset() async {
    setState(() => _busy = true);
    try {
      await ref.read(authProvider.notifier).wipe();
      ref.invalidate(authProvider);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final corrupted = isStorageCorruptionError(widget.error);
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      child: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                corrupted ? Icons.restore : Icons.error_outline,
                size: 56,
                color: corrupted ? scheme.primary : scheme.error,
              ),
              const SizedBox(height: 16),
              Text(
                corrupted
                    ? "Couldn't open your saved wallet"
                    : 'Something went wrong',
                style: Theme.of(context).textTheme.titleLarge,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              Text(
                corrupted
                    ? 'Your saved wallet data could not be unlocked. This usually '
                        'happens after an Android system backup or device-to-device '
                        'transfer to a new phone.\n\n'
                        'Your funds are safe on-chain. Reset the app and re-import '
                        'your recovery phrase to restore access.'
                    : 'The wallet hit an unexpected error while starting.\n\n'
                        '${widget.error}',
                style: Theme.of(context).textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 28),
              FilledButton.icon(
                onPressed: _busy ? null : _reset,
                icon: _busy
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
                label: Text(
                  corrupted ? 'Reset & re-import wallet' : 'Reset app',
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Reset only clears this device — nothing on-chain is affected.',
                style: Theme.of(context).textTheme.bodySmall,
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
