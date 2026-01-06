/// Wallet service for balance and transaction operations
library;

import 'package:logging/logging.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'rpc_service.dart';

class WalletService {
  final RpcService rpcService;
  final _log = Logger('WalletService');
  final _storage = const FlutterSecureStorage();

  static const String _addressKey = 'wallet_address';
  static const String _privateKeyKey = 'wallet_private_key';

  WalletService({required this.rpcService});

  /// Get wallet address from secure storage
  Future<String?> getAddress() async {
    try {
      return await _storage.read(key: _addressKey);
    } catch (e, stackTrace) {
      _log.warning('Failed to read wallet address', e, stackTrace);
      return null;
    }
  }

  /// Save wallet address to secure storage
  Future<void> saveAddress(String address) async {
    try {
      await _storage.write(key: _addressKey, value: address);
      _log.info('Wallet address saved');
    } catch (e, stackTrace) {
      _log.severe('Failed to save wallet address', e, stackTrace);
      rethrow;
    }
  }

  /// Get balance for the wallet address
  Future<int> getBalance(String address) async {
    try {
      return await rpcService.getBalance(address);
    } catch (e, stackTrace) {
      _log.warning('Failed to get balance', e, stackTrace);
      rethrow;
    }
  }

  /// Get nonce (transaction count) for the wallet address
  Future<int> getNonce(String address) async {
    try {
      return await rpcService.getTransactionCount(address);
    } catch (e, stackTrace) {
      _log.warning('Failed to get nonce', e, stackTrace);
      rethrow;
    }
  }

  /// Send transaction (placeholder - requires signing implementation)
  Future<String> sendTransaction({
    required String from,
    required String to,
    required int value,
    int? nonce,
    int? gasLimit,
    int? gasPrice,
  }) async {
    try {
      // TODO: Implement transaction signing with PQ crypto
      // This is a placeholder that shows the structure
      _log.info('Preparing transaction: $from -> $to (value: $value)');
      
      // Get nonce if not provided
      nonce ??= await getNonce(from);

      // Build transaction
      final tx = {
        'from': from,
        'to': to,
        'value': '0x${value.toRadixString(16)}',
        'nonce': '0x${nonce.toRadixString(16)}',
        if (gasLimit != null) 'gas': '0x${gasLimit.toRadixString(16)}',
        if (gasPrice != null) 'gasPrice': '0x${gasPrice.toRadixString(16)}',
      };

      _log.fine('Transaction: $tx');

      // Sign transaction (TODO: implement PQ signing)
      final signedTx = await _signTransaction(tx);

      // Send signed transaction
      final txHash = await rpcService.sendRawTransaction(signedTx);
      _log.info('Transaction sent: $txHash');
      
      return txHash;
    } catch (e, stackTrace) {
      _log.severe('Failed to send transaction', e, stackTrace);
      rethrow;
    }
  }

  /// Sign transaction (placeholder for PQ crypto implementation)
  Future<String> _signTransaction(Map<String, dynamic> tx) async {
    // TODO: Implement Dilithium3/SPHINCS+ signing
    // This would:
    // 1. Load private key from secure storage
    // 2. Encode transaction to canonical format
    // 3. Sign with PQ signature algorithm
    // 4. Return hex-encoded signed transaction
    
    throw UnimplementedError('Transaction signing not yet implemented');
  }

  /// Import wallet from private key
  Future<void> importWallet(String privateKey, String address) async {
    try {
      await _storage.write(key: _privateKeyKey, value: privateKey);
      await _storage.write(key: _addressKey, value: address);
      _log.info('Wallet imported successfully');
    } catch (e, stackTrace) {
      _log.severe('Failed to import wallet', e, stackTrace);
      rethrow;
    }
  }

  /// Create new wallet (placeholder)
  Future<WalletInfo> createWallet() async {
    // TODO: Implement PQ key generation
    throw UnimplementedError('Wallet creation not yet implemented');
  }

  /// Check if wallet exists
  Future<bool> hasWallet() async {
    try {
      final address = await _storage.read(key: _addressKey);
      return address != null && address.isNotEmpty;
    } catch (e) {
      return false;
    }
  }

  /// Clear wallet data (logout)
  Future<void> clearWallet() async {
    try {
      await _storage.delete(key: _addressKey);
      await _storage.delete(key: _privateKeyKey);
      _log.info('Wallet cleared');
    } catch (e, stackTrace) {
      _log.severe('Failed to clear wallet', e, stackTrace);
      rethrow;
    }
  }
}

/// Wallet information
class WalletInfo {
  final String address;
  final String publicKey;
  final String privateKey;

  WalletInfo({
    required this.address,
    required this.publicKey,
    required this.privateKey,
  });
}
