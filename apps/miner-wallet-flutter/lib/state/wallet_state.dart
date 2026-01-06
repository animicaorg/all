/// Wallet state providers
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:logging/logging.dart';

import '../services/wallet_service.dart';
import 'app_state.dart';

/// Wallet service provider
final walletServiceProvider = Provider<WalletService>((ref) {
  final rpc = ref.watch(rpcServiceProvider);
  return WalletService(rpcService: rpc);
});

/// Wallet address provider
final walletAddressProvider = FutureProvider<String?>((ref) async {
  final walletService = ref.watch(walletServiceProvider);
  return await walletService.getAddress();
});

/// Wallet balance provider
final walletBalanceProvider = FutureProvider<int>((ref) async {
  final walletService = ref.watch(walletServiceProvider);
  final address = await ref.watch(walletAddressProvider.future);
  
  if (address == null || address.isEmpty) {
    return 0;
  }

  try {
    return await walletService.getBalance(address);
  } catch (e) {
    return 0;
  }
});

/// Wallet nonce provider
final walletNonceProvider = FutureProvider<int>((ref) async {
  final walletService = ref.watch(walletServiceProvider);
  final address = await ref.watch(walletAddressProvider.future);
  
  if (address == null || address.isEmpty) {
    return 0;
  }

  try {
    return await walletService.getNonce(address);
  } catch (e) {
    return 0;
  }
});

/// Wallet exists provider
final hasWalletProvider = FutureProvider<bool>((ref) async {
  final walletService = ref.watch(walletServiceProvider);
  return await walletService.hasWallet();
});

/// Send transaction notifier
final sendTransactionProvider = StateNotifierProvider<SendTransactionNotifier, AsyncValue<String?>>((ref) {
  return SendTransactionNotifier(ref.watch(walletServiceProvider));
});

class SendTransactionNotifier extends StateNotifier<AsyncValue<String?>> {
  final WalletService _walletService;
  final _log = Logger('SendTransactionNotifier');

  SendTransactionNotifier(this._walletService) : super(const AsyncValue.data(null));

  Future<void> sendTransaction({
    required String from,
    required String to,
    required int value,
    int? nonce,
  }) async {
    state = const AsyncValue.loading();

    try {
      _log.info('Sending transaction: $from -> $to (value: $value)');
      final txHash = await _walletService.sendTransaction(
        from: from,
        to: to,
        value: value,
        nonce: nonce,
      );
      
      state = AsyncValue.data(txHash);
      _log.info('Transaction sent successfully: $txHash');
    } catch (e, stackTrace) {
      _log.severe('Failed to send transaction', e, stackTrace);
      state = AsyncValue.error(e, stackTrace);
    }
  }

  void reset() {
    state = const AsyncValue.data(null);
  }
}

/// Import wallet notifier
final importWalletProvider = StateNotifierProvider<ImportWalletNotifier, AsyncValue<void>>((ref) {
  return ImportWalletNotifier(ref.watch(walletServiceProvider));
});

class ImportWalletNotifier extends StateNotifier<AsyncValue<void>> {
  final WalletService _walletService;
  final _log = Logger('ImportWalletNotifier');

  ImportWalletNotifier(this._walletService) : super(const AsyncValue.data(null));

  Future<void> importWallet(String privateKey, String address) async {
    state = const AsyncValue.loading();

    try {
      _log.info('Importing wallet: $address');
      await _walletService.importWallet(privateKey, address);
      state = const AsyncValue.data(null);
      _log.info('Wallet imported successfully');
    } catch (e, stackTrace) {
      _log.severe('Failed to import wallet', e, stackTrace);
      state = AsyncValue.error(e, stackTrace);
    }
  }

  void reset() {
    state = const AsyncValue.data(null);
  }
}
