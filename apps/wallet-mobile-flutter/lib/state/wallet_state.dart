// Riverpod providers wiring the vault + RPC client into the UI.

import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'dart:math' as math;

import '../constants.dart';
import '../models/account.dart';
import '../services/ml_dsa_65.dart';
import '../services/rpc.dart';
import 'auth_state.dart';

export 'auth_state.dart' show vaultProvider, authProvider, authServiceProvider;

final rpcProvider = Provider<RpcClient>((ref) {
  final c = RpcClient();
  ref.onDispose(c.close);
  return c;
});

/// Loads accounts from secure storage on first read. Auto-reloads whenever
/// the vault provider's unlock key changes.
class AccountsNotifier extends AsyncNotifier<List<Account>> {
  @override
  Future<List<Account>> build() async {
    final vault = ref.watch(vaultProvider);
    final auth = ref.watch(authProvider).value;
    // Don't try to decrypt a configured vault without the key.
    if (auth != null && auth.configured && !auth.unlocked) return const [];
    return vault.load();
  }

  /// Create a new ML-DSA-65 (FIPS 204) account — chain v2 default.
  Future<Account> createMlDsa65Account(String label) async {
    // Publish loading/error into the provider. A method that merely THROWS never
    // becomes AsyncError, so the screens' existing `error:` branches never fired
    // and a failed creation was indistinguishable from a no-op: 0.2.3 shipped with
    // keygen throwing on every call (missing atob/btoa in the JS engine) and the
    // button looked simply dead. Whatever breaks next must be visible.
    state = const AsyncLoading<List<Account>>().copyWithPrevious(state);
    try {
      final rnd = math.Random.secure();
      final seed = Uint8List.fromList(
        List<int>.generate(MlDsa65.seedLen, (_) => rnd.nextInt(256)),
      );
      final kp = await MlDsa65.keygen(seed);
      final acc = Account(
        label: label,
        algId: AnimicaConfig.algIdMlDsa65,
        publicKey: kp.publicKey,
        secretKey: kp.secretKey,
      );
      await ref.read(vaultProvider).add(acc);
      state = AsyncData([...?state.value, acc]);
      return acc;
    } catch (e, st) {
      state = AsyncError<List<Account>>(e, st).copyWithPrevious(state);
      rethrow; // callers show the message; the provider records it.
    }
  }

  // Legacy stub-scheme creation (sphincs_shake_128s / dilithium3) has been
  // removed: those schemes are forgeable, rejected by the node, and permanently
  // strand funds. New wallets are always ML-DSA-65 (createMlDsa65Account).
  // Existing legacy wallets can still be imported and read via importFromHex.

  Future<Account> importFromHex({
    required String label,
    required int algId,
    required Uint8List publicKey,
    required Uint8List secretKey,
  }) async {
    final acc = Account(
      label: label,
      algId: algId,
      publicKey: publicKey,
      secretKey: secretKey,
    );
    await ref.read(vaultProvider).add(acc);
    state = AsyncData([...?state.value, acc]);
    return acc;
  }

  Future<void> addAll(List<Account> accounts) async {
    final vault = ref.read(vaultProvider);
    for (final a in accounts) {
      await vault.add(a);
    }
    state = AsyncData(await vault.load());
  }

  Future<void> remove(String address) async {
    await ref.read(vaultProvider).remove(address);
    state = AsyncData(
      (state.value ?? const []).where((a) => a.address != address).toList(),
    );
  }
}

final accountsProvider =
    AsyncNotifierProvider<AccountsNotifier, List<Account>>(AccountsNotifier.new);

/// The "active" account address — drives the home screen, send-from, etc.
final activeAddressProvider = StateProvider<String?>((ref) {
  final accs = ref.watch(accountsProvider).value;
  if (accs == null || accs.isEmpty) return null;
  return accs.first.address;
});

final activeAccountProvider = Provider<Account?>((ref) {
  final addr = ref.watch(activeAddressProvider);
  final accs = ref.watch(accountsProvider).value;
  if (addr == null || accs == null) return null;
  for (final a in accs) {
    if (a.address == addr) return a;
  }
  return null;
});

final balanceProvider = FutureProvider.autoDispose<BigInt>((ref) async {
  final acc = ref.watch(activeAccountProvider);
  if (acc == null) return BigInt.zero;
  return ref.read(rpcProvider).getBalance(acc.address);
});
