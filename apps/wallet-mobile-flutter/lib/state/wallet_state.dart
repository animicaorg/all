// Riverpod providers wiring the vault + RPC client into the UI.

import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../constants.dart';
import '../models/account.dart';
import '../services/keys.dart';
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

  Future<Account> createSphincsAccount(String label) async {
    final kp = generateSphincsKeypair();
    final acc = Account(
      label: label,
      algId: AnimicaConfig.algIdSphincs,
      publicKey: kp.publicKey,
      secretKey: kp.secretKey,
    );
    await ref.read(vaultProvider).add(acc);
    state = AsyncData([...?state.value, acc]);
    return acc;
  }

  Future<Account> createDilithium3Account(String label) async {
    final kp = generateDilithium3Keypair();
    final acc = Account(
      label: label,
      algId: AnimicaConfig.algIdDilithium3,
      publicKey: kp.publicKey,
      secretKey: kp.secretKey,
    );
    await ref.read(vaultProvider).add(acc);
    state = AsyncData([...?state.value, acc]);
    return acc;
  }

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
