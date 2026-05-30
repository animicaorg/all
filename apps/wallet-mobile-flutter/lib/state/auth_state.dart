// Auth state: holds the in-memory unlock key + whether a password is set.
//
// We avoid persisting the unlock key — it lives in RAM for the duration
// of the app session. When the app is backgrounded the OS may clear
// memory, which is fine; the user re-enters their password on resume.

import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/auth.dart';
import '../services/vault.dart';

final authServiceProvider = Provider<AuthService>((_) => AuthService());

class AuthStatus {
  final bool configured;
  final bool unlocked;
  final Uint8List? unlockKey;

  const AuthStatus({
    required this.configured,
    required this.unlocked,
    this.unlockKey,
  });

  factory AuthStatus.locked({required bool configured}) =>
      AuthStatus(configured: configured, unlocked: false);

  factory AuthStatus.unlocked(Uint8List key) =>
      AuthStatus(configured: true, unlocked: true, unlockKey: key);
}

class AuthNotifier extends AsyncNotifier<AuthStatus> {
  @override
  Future<AuthStatus> build() async {
    final configured = await ref.read(authServiceProvider).isConfigured();
    return AuthStatus.locked(configured: configured);
  }

  Future<void> setupPassword(String password) async {
    final key = await ref.read(authServiceProvider).setPassword(password);
    state = AsyncData(AuthStatus.unlocked(key));
  }

  /// Returns true on success.
  Future<bool> unlock(String password) async {
    final key = await ref.read(authServiceProvider).verify(password);
    if (key == null) return false;
    state = AsyncData(AuthStatus.unlocked(key));
    return true;
  }

  void lock() {
    final cfg = state.value?.configured ?? false;
    state = AsyncData(AuthStatus.locked(configured: cfg));
  }

  Future<void> wipe() async {
    await ref.read(authServiceProvider).wipeAll();
    state = const AsyncData(AuthStatus(configured: false, unlocked: false));
  }
}

final authProvider = AsyncNotifierProvider<AuthNotifier, AuthStatus>(AuthNotifier.new);

/// Vault that uses the current unlock key. Watching this auto-refreshes
/// on lock/unlock.
final vaultProvider = Provider<Vault>((ref) {
  final auth = ref.watch(authProvider).value;
  return Vault(unlockKey: auth?.unlockKey);
});
