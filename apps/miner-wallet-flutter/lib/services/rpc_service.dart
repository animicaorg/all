/// JSON-RPC client service for Animica node communication
library;

import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:logging/logging.dart';

class RpcService {
  final String rpcUrl;
  final Duration timeout;
  final _log = Logger('RpcService');
  
  int _requestId = 0;

  RpcService({
    required this.rpcUrl,
    this.timeout = const Duration(seconds: 30),
  });

  /// Make a JSON-RPC call
  Future<dynamic> call(String method, [List<dynamic>? params]) async {
    final id = ++_requestId;
    
    final request = {
      'jsonrpc': '2.0',
      'id': id,
      'method': method,
      if (params != null && params.isNotEmpty) 'params': params,
    };

    _log.fine('RPC request: $method (id=$id)');

    try {
      final response = await http
          .post(
            Uri.parse(rpcUrl),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(request),
          )
          .timeout(timeout);

      if (response.statusCode != 200) {
        throw RpcException(
          'HTTP ${response.statusCode}: ${response.reasonPhrase}',
          code: response.statusCode,
        );
      }

      final responseData = jsonDecode(response.body) as Map<String, dynamic>;

      if (responseData.containsKey('error')) {
        final error = responseData['error'] as Map<String, dynamic>;
        throw RpcException(
          error['message'] as String? ?? 'Unknown RPC error',
          code: error['code'] as int?,
          data: error['data'],
        );
      }

      return responseData['result'];
    } on TimeoutException {
      throw RpcException('Request timeout after ${timeout.inSeconds}s');
    } catch (e) {
      if (e is RpcException) rethrow;
      _log.warning('RPC call failed: $method', e);
      throw RpcException('RPC call failed: $e');
    }
  }

  /// Get chain ID
  Future<int> getChainId() async {
    final result = await call('eth_chainId');
    if (result is String) {
      return int.parse(result.replaceFirst('0x', ''), radix: 16);
    }
    return result as int;
  }

  /// Get block number (height)
  Future<int> getBlockNumber() async {
    final result = await call('eth_blockNumber');
    if (result is String) {
      return int.parse(result.replaceFirst('0x', ''), radix: 16);
    }
    return result as int;
  }

  /// Get balance for an address
  Future<int> getBalance(String address) async {
    final result = await call('eth_getBalance', [address, 'latest']);
    if (result is String) {
      return int.parse(result.replaceFirst('0x', ''), radix: 16);
    }
    return result as int;
  }

  /// Get transaction count (nonce) for an address
  Future<int> getTransactionCount(String address) async {
    final result = await call('eth_getTransactionCount', [address, 'latest']);
    if (result is String) {
      return int.parse(result.replaceFirst('0x', ''), radix: 16);
    }
    return result as int;
  }

  /// Send raw transaction
  Future<String> sendRawTransaction(String signedTx) async {
    final result = await call('eth_sendRawTransaction', [signedTx]);
    return result as String;
  }

  /// Get sync status
  Future<SyncStatus> getSyncStatus() async {
    final result = await call('eth_syncing');
    
    if (result == false) {
      return SyncStatus(syncing: false);
    }

    final data = result as Map<String, dynamic>;
    return SyncStatus(
      syncing: true,
      startingBlock: _parseHexInt(data['startingBlock']),
      currentBlock: _parseHexInt(data['currentBlock']),
      highestBlock: _parseHexInt(data['highestBlock']),
    );
  }

  /// Get mining template (for mining operations)
  Future<Map<String, dynamic>> getMiningTemplate(String payoutAddress) async {
    final result = await call('miner_getTemplate', [payoutAddress]);
    return result as Map<String, dynamic>;
  }

  /// Submit mining share
  Future<bool> submitShare(Map<String, dynamic> share) async {
    final result = await call('miner_submitShare', [share]);
    return result as bool;
  }

  /// Get peer count
  Future<int> getPeerCount() async {
    final result = await call('net_peerCount');
    if (result is String) {
      return int.parse(result.replaceFirst('0x', ''), radix: 16);
    }
    return result as int;
  }

  int _parseHexInt(dynamic value) {
    if (value is String) {
      return int.parse(value.replaceFirst('0x', ''), radix: 16);
    }
    return value as int;
  }
}

/// Sync status information
class SyncStatus {
  final bool syncing;
  final int? startingBlock;
  final int? currentBlock;
  final int? highestBlock;

  SyncStatus({
    required this.syncing,
    this.startingBlock,
    this.currentBlock,
    this.highestBlock,
  });

  double? get progress {
    if (!syncing || currentBlock == null || highestBlock == null) {
      return null;
    }
    if (highestBlock == 0) return 0.0;
    return currentBlock! / highestBlock!;
  }
}

/// RPC exception
class RpcException implements Exception {
  final String message;
  final int? code;
  final dynamic data;

  RpcException(this.message, {this.code, this.data});

  @override
  String toString() => 'RpcException: $message${code != null ? ' (code: $code)' : ''}';
}
