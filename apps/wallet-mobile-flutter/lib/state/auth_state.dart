// Auth state: holds the in-memory unlock key + whether a password is set.
//
// We avoid persisting the unlock key — it lives in RAM for the duration
// of the app session. When the app is backgrounded the OS may clear
// memory, which is fine; the user re-enters their password on resume.
//
// Biometric unlock (fingerprint / Face ID) stores a copy of the derived key in
// secure storage and reads it back after a biometric prompt — the password
// stays a full fallback. See services/biometric.dart.

import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/auth.dart';
import '../services/biometric.dart';
import '../services/vault.dart';

final authServiceProvider = Provider<AuthService>((_) => AuthService());
final biometricServiceProvider = Provider<BiometricService>((_) => BiometricService());

class AuthStatus {
  final bool configured;
  final bool unlocked;
  final Uint8List? unlockKey;

  /// Whether a biometric-unlock key is stored on this device.
  final bool biometricEnabled;

  /// Whether the device can prompt for biometrics at all (hardware + enrolled).
  final bool biometricAvailable;

  const AuthStatus({
    required this.configured,
    required this.unlocked,
    this.unlockKey,
    this.biometricEnabled = false,
    this.biometricAvailable = false,
  });

  factory AuthStatus.locked({
    required bool configured,
    bool biometricEnabled = false,
    bool biometricAvailable = false,
  }) =>
      AuthStatus(
        configured: configured,
        unlocked: false,
        biometricEnabled: biometricEnabled,
        biometricAvailable: biometricAvailable,
      );

  AuthStatus copyWith({
    bool? unlocked,
    Uint8List? unlockKey,
    bool? biometricEnabled,
    bool? biometricAvailable,
  }) =>
      AuthStatus(
        configured: configured,
        unlocked: unlocked ?? this.unlocked,
        unlockKey: unlockKey ?? this.unlockKey,
        biometricEnabled: biometricEnabled ?? this.biometricEnabled,
        biometricAvailable: biometricAvailable ?? this.biometricAvailable,
      );
}

class AuthNotifier extends AsyncNotifier<AuthStatus> {
  @override
  Future<AuthStatus> build() async {
    final auth = ref.read(authServiceProvider);
    final configured = await auth.isConfigured();
    final biometricEnabled = await auth.hasBiometricKey();
    final biometricAvailable = await ref.read(biometricServiceProvider).isAvailable();
    return AuthStatus.locked(
      configured: configured,
      biometricEnabled: biometricEnabled,
      biometricAvailable: biometricAvailable,
    );
  }

  Future<void> setupPassword(String password) async {
    final key = await ref.read(authServiceProvider).setPassword(password);
    final prev = state.value;
    state = AsyncData(AuthStatus(
      configured: true,
      unlocked: true,
      unlockKey: key,
      biometricEnabled: false,
      biometricAvailable: prev?.biometricAvailable ?? false,
    ));
  }

  /// Returns true on success.
  Future<bool> unlock(String password) async {
    try {
      final key = await ref.read(authServiceProvider).verify(password);
      if (key == null) return false;
      _setUnlocked(key);
      return true;
    } on WalletStorageCorruptedException catch (e, st) {
      // Undecryptable local storage — route to the recovery screen.
      state = AsyncError(e, st);
      return false;
    }
  }

  /// Unlock via a biometric prompt using the stored key. Returns:
  ///   true  — authenticated and unlocked
  ///   false — biometrics unavailable/declined, or the stored key is stale
  ///           (password change); the caller falls back to the password.
  Future<bool> unlockWithBiometric() async {
    final auth = ref.read(authServiceProvider);
    if (!await auth.hasBiometricKey()) return false;
    final ok = await ref
        .read(biometricServiceProvider)
        .authenticate('Unlock your Animica wallet');
    if (!ok) return false;
    final key = await auth.readBiometricKey();
    if (key == null) return false;
    // A password change would leave a stale key behind; verify before trusting.
    if (!await auth.keyMatchesVerifier(key)) {
      await auth.clearBiometricKey();
      final cfg = state.value;
      if (cfg != null) {
        state = AsyncData(cfg.copyWith(biometricEnabled: false));
      }
      return false;
    }
    _setUnlocked(key);
    return true;
  }

  /// Enable biometric unlock. Requires the wallet to be unlocked (so we have
  /// the derived key) and a confirming biometric prompt. Returns true on
  /// success.
  Future<bool> enableBiometric() async {
    final cur = state.value;
    final key = cur?.unlockKey;
    if (key == null) return false; // must be unlocked
    final ok = await ref
        .read(biometricServiceProvider)
        .authenticate('Confirm to enable biometric unlock');
    if (!ok) return false;
    await ref.read(authServiceProvider).storeBiometricKey(key);
    state = AsyncData(cur!.copyWith(biometricEnabled: true));
    return true;
  }

  Future<void> disableBiometric() async {
    await ref.read(authServiceProvider).clearBiometricKey();
    final cur = state.value;
    if (cur != null) state = AsyncData(cur.copyWith(biometricEnabled: false));
  }

  void _setUnlocked(Uint8List key) {
    final prev = state.value;
    state = AsyncData(AuthStatus(
      configured: true,
      unlocked: true,
      unlockKey: key,
      biometricEnabled: prev?.biometricEnabled ?? false,
      biometricAvailable: prev?.biometricAvailable ?? false,
    ));
  }

  void lock() {
    final prev = state.value;
    state = AsyncData(AuthStatus.locked(
      configured: prev?.configured ?? false,
      biometricEnabled: prev?.biometricEnabled ?? false,
      biometricAvailable: prev?.biometricAvailable ?? false,
    ));
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
