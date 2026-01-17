import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/services/state_service.dart';
import 'package:animica_wallet/services/rpc_client.dart';
import 'package:animica_wallet/state/account_state.dart';
import 'package:animica_wallet/state/providers.dart';

class FakeStateService implements StateService {
  @override
  final RpcClient rpc;

  BalanceContext context;
  BigInt balance;
  int nonce;
  Object? lastBalanceTag;
  Object? lastNonceTag;

  FakeStateService({
    required this.context,
    required this.balance,
    required this.nonce,
  }) : rpc = RpcClient(endpoint: Uri.parse('http://localhost:8545'));

  @override
  Future<BigInt> getBalance(String address, {BlockTag at = 'latest'}) async {
    lastBalanceTag = at;
    return balance;
  }

  @override
  Future<int> getNonce(String address, {BlockTag at = 'latest'}) async {
    lastNonceTag = at;
    return nonce;
  }

  @override
  Future<int> getBlockNumber() async {
    return context.queriedHeight ?? 0;
  }

  @override
  Future<BalanceContext> getBalanceContext() async {
    return context;
  }

  @override
  void close() {}
}

void main() {
  test('refreshBalance anchors to best block height and flags syncing', () async {
    final fake = FakeStateService(
      context: const BalanceContext(
        source: 'chain_state',
        queriedHeight: 10,
        queriedHash: '0xabc',
        bestBlockHeight: 10,
        bestBlockHash: '0xabc',
        bestHeaderHeight: 12,
        isSyncing: true,
      ),
      balance: BigInt.from(42),
      nonce: 7,
    );
    final container = createContainer(overrides: MyOverrides(state: fake));
    addTearDown(container.dispose);

    final notifier = container.read(accountsStateProvider.notifier);
    final address = notifier.addAccount(address: 'anim1testaddress', label: 'A');
    await notifier.refreshBalance(address);

    final state = container.read(accountsStateProvider);
    final bal = state.balances[address];
    expect(fake.lastBalanceTag, 10);
    expect(fake.lastNonceTag, 10);
    expect(bal?.amount, BigInt.from(42));
    expect(bal?.queriedHeight, 10);
    expect(bal?.isSyncing, true);
  });

  test('refreshBalance updates when head advances', () async {
    final fake = FakeStateService(
      context: const BalanceContext(
        source: 'chain_state',
        queriedHeight: 5,
        queriedHash: '0x111',
        bestBlockHeight: 5,
        bestBlockHash: '0x111',
        bestHeaderHeight: 5,
        isSyncing: false,
      ),
      balance: BigInt.from(100),
      nonce: 1,
    );
    final container = createContainer(overrides: MyOverrides(state: fake));
    addTearDown(container.dispose);

    final notifier = container.read(accountsStateProvider.notifier);
    final address = notifier.addAccount(address: 'anim1testaddress2', label: 'B');
    await notifier.refreshBalance(address);

    fake.context = const BalanceContext(
      source: 'chain_state',
      queriedHeight: 6,
      queriedHash: '0x222',
      bestBlockHeight: 6,
      bestBlockHash: '0x222',
      bestHeaderHeight: 6,
      isSyncing: false,
    );
    fake.balance = BigInt.from(150);
    await notifier.refreshBalance(address);

    final state = container.read(accountsStateProvider);
    final bal = state.balances[address];
    expect(bal?.amount, BigInt.from(150));
    expect(bal?.queriedHeight, 6);
  });
}
