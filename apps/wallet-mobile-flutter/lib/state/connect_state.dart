// Riverpod wiring for the site-pairing ("Connect to site") feature.
//
// Holds the one active session, and runs the poller that pulls queued
// sign/send requests off the relay. The poller only publishes requests —
// approving them is the UI's job, so nothing is ever signed without a sheet
// in front of the user.

library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/wallet_connect.dart';

final walletConnectApiProvider = Provider<WalletConnectApi>((ref) {
  final api = WalletConnectApi();
  return api;
});

final walletConnectStoreProvider = Provider<WalletConnectStore>((ref) {
  return WalletConnectStore();
});

/// The active pairing, or null when not connected to any site.
class WalletConnectSessionNotifier extends StateNotifier<WalletConnectSession?> {
  final Ref _ref;
  WalletConnectSessionNotifier(this._ref) : super(null) {
    _restore();
  }

  Future<void> _restore() async {
    state = await _ref.read(walletConnectStoreProvider).load();
  }

  Future<void> set(WalletConnectSession session) async {
    await _ref.read(walletConnectStoreProvider).save(session);
    state = session;
  }

  Future<void> clear() async {
    await _ref.read(walletConnectStoreProvider).clear();
    state = null;
  }
}

final walletConnectSessionProvider =
    StateNotifierProvider<WalletConnectSessionNotifier, WalletConnectSession?>(
  (ref) => WalletConnectSessionNotifier(ref),
);

/// Requests the paired site is waiting on.
///
/// Emits an empty list while disconnected. Network errors are swallowed and
/// retried on the next tick — a flaky connection should not tear the session
/// down, it should just mean "nothing new yet".
final pendingWalletRequestsProvider =
    StreamProvider.autoDispose<List<WalletConnectRequest>>((ref) async* {
  final session = ref.watch(walletConnectSessionProvider);
  if (session == null) {
    yield const [];
    return;
  }
  final api = ref.watch(walletConnectApiProvider);

  while (true) {
    try {
      yield await api.pending(session);
    } catch (_) {
      // Keep the stream alive; the next poll may succeed.
    }
    await Future<void>.delayed(kWalletConnectPollInterval);
  }
});
