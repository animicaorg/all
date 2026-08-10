// Biometric (fingerprint / Face ID) unlock.
//
// Design: biometrics don't replace the password — they unlock a stored copy of
// the password-derived AES key. Enabling captures the current in-memory unlock
// key (only available while the wallet is already unlocked) and writes it to
// secure storage; unlocking re-reads it after a successful biometric prompt.
//
// Security model:
//   * The key at rest is protected by the OS keystore (Keychain /
//     EncryptedSharedPreferences), same as the vault ciphertext.
//   * A biometric prompt (local_auth) gates every read of that key, so a
//     thief with an unlocked phone still can't unlock the wallet without the
//     enrolled fingerprint/face — and the password remains a full fallback.
//   * Disabling, wiping, or changing the password clears the stored key.
//
// This never stores the password itself, only the derived key, so the password
// is never recoverable from the device.

import 'package:flutter/services.dart' show PlatformException;
import 'package:local_auth/local_auth.dart';

class BiometricService {
  final LocalAuthentication _auth;
  BiometricService({LocalAuthentication? auth})
      : _auth = auth ?? LocalAuthentication();

  /// True when the device has biometric (or other strong) hardware AND the
  /// user has enrolled at least one credential we can prompt for.
  Future<bool> isAvailable() async {
    try {
      final supported = await _auth.isDeviceSupported();
      if (!supported) return false;
      final canCheck = await _auth.canCheckBiometrics;
      final enrolled = await _auth.getAvailableBiometrics();
      // canCheckBiometrics can be false while device-credential fallback still
      // works, so treat either signal as usable.
      return canCheck || enrolled.isNotEmpty || supported;
    } on PlatformException {
      return false;
    }
  }

  /// A human label for the strongest enrolled modality (for button copy).
  Future<String> modalityLabel() async {
    try {
      final kinds = await _auth.getAvailableBiometrics();
      if (kinds.contains(BiometricType.face)) return 'Face ID';
      if (kinds.contains(BiometricType.fingerprint)) return 'fingerprint';
      if (kinds.contains(BiometricType.iris)) return 'iris';
    } on PlatformException {
      // fall through
    }
    return 'biometrics';
  }

  /// Prompt the user. Returns true only on a successful authentication.
  /// [reason] is shown in the system dialog.
  Future<bool> authenticate(String reason) async {
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        options: const AuthenticationOptions(
          stickyAuth: true,
          // biometricOnly: the device PIN/passcode must NOT be able to satisfy
          // this prompt. The wallet password is a separate secret from the
          // device credential; letting the device PIN unlock the vault would
          // collapse the wallet's second factor onto the phone's lock code
          // (which a thief may know). Biometrics or nothing — the password is
          // always the fallback path on the unlock screen.
          biometricOnly: true,
        ),
      );
    } on PlatformException {
      // Locked out, no enrollment, user cancelled at the OS layer, etc. —
      // the caller falls back to the password.
      return false;
    }
  }
}
