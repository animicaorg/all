/*
 * Animica Wallet — StateService
 *
 * Thin wrappers around JSON-RPC to fetch frequently used chain state:
 *   • getBalance(am1… address [, at=latest]) → BigInt (wei-like smallest unit)
 *   • getNonce(am1… address [, at=latest])   → int    (transaction sequence)
 *   • getBlockNumber()                       → int    (current head height)
 *
 * RPC method names follow the Animica-prefixed style used elsewhere:
 *   - animica_getBalance(address, blockTag)
 *   - animica_getNonce(address, blockTag)
 *   - animica_blockNumber()
 *
 * If your node exposes Ethereum-compatible names, you can switch to:
 *   - eth_getBalance / eth_getTransactionCount / eth_blockNumber
 * by toggling the constants below.
 */

import 'dart:async';
import 'dart:convert';

import 'rpc_client.dart';

const _USE_ETH_NAMES = false;

final _M_GET_BALANCE     = _USE_ETH_NAMES ? 'eth_getBalance' : 'animica_getBalance';
final _M_GET_NONCE       = _USE_ETH_NAMES ? 'eth_getTransactionCount' : 'animica_getNonce';
final _M_BLOCK_NUMBER    = _USE_ETH_NAMES ? 'eth_blockNumber' : 'animica_blockNumber';

/// Block tag values typically: 'latest' | 'pending' | explicit height (0xHEX or int)
typedef BlockTag = Object?; // String tag or int/hex string

class StateService {
  final RpcClient rpc;

  StateService(this.rpc);

  factory StateService.fromEnv() => StateService(RpcClient.fromEnv());

  /// Get native balance of an account in the smallest unit (BigInt).
  /// [at] can be 'latest' (default), 'pending', an int height, or a 0xHEX height.
  Future<BigInt> getBalance(String address, {BlockTag at = 'latest'}) async {
    final params = _USE_ETH_NAMES
        ? [address, _normalizeBlockTag(at)]
        : [address, _normalizeBlockTag(at)];
    final res = await rpc.call<dynamic>(_M_GET_BALANCE, params);
    return _parseBigInt(res);
  }

  /// Get the account's transaction nonce (sequence).
  Future<int> getNonce(String address, {BlockTag at = 'latest'}) async {
    final params = _USE_ETH_NAMES
        ? [address, _normalizeBlockTag(at)]
        : [address, _normalizeBlockTag(at)];
    final res = await rpc.call<dynamic>(_M_GET_NONCE, params);
    return _parseInt(res);
  }

  /// Current head height (block number).
  Future<int> getBlockNumber() async {
    final res = await rpc.call<dynamic>(_M_BLOCK_NUMBER);
    return _parseInt(res);
  }

  // ---------------- Helpers ----------------

  // Accepts 'latest' / 'pending' / numeric / 0xHEX string.
  Object _normalizeBlockTag(BlockTag tag) {
    if (tag == null) return 'latest';
    if (tag is String) {
      final s = tag.trim();
      if (s.isEmpty) return 'latest';
      return s;
    }
    if (tag is int) {
      // Some nodes accept plain number; others want 0x hex.
      // We return hex to be maximally compatible.
      return '0x${tag.toRadixString(16)}';
    }
    // Unknown type → JSON-encodable as-is
    return tag;
  }

  BigInt _parseBigInt(dynamic v) {
    if (v == null) {
      throw FormatException('Balance response is null');
    }
    if (v is BigInt) return v;
    if (v is int) return BigInt.from(v);
    if (v is String) {
      final s = v.trim();
      if (s.isEmpty) {
        throw FormatException('Balance response is empty string');
      }
      if (s.startsWith('0x') || s.startsWith('0X')) {
        final hex = s.substring(2).isEmpty ? '0' : s.substring(2);
        try {
          return BigInt.parse(hex, radix: 16);
        } catch (e) {
          throw FormatException('Balance response is invalid hex: "$s" - $e');
        }
      }
      // Try decimal first
      try {
        return BigInt.parse(s);
      } catch (_) {
        // Maybe it's JSON-encoded number
        try {
          return BigInt.parse(json.decode(s).toString(), radix: 10);
        } catch (e) {
          throw FormatException('Balance response is invalid decimal: "$s" - $e');
        }
      }
    }
    // Object response: check for common field names
    if (v is Map) {
      // Try common field names
      for (final field in ['balance', 'value', 'amount', 'result']) {
        if (v.containsKey(field)) {
          try {
            return _parseBigInt(v[field]);
          } catch (_) {
            // Continue to next field
          }
        }
      }
      throw FormatException(
        'Balance response is object but no recognized field (balance/value/amount/result). '
        'Response: ${json.encode(v).substring(0, 200)}'
      );
    }
    // Last resort: toString and try both hex/dec
    final t = v.toString();
    if (t.startsWith('0x') || t.startsWith('0X')) {
      try {
        return BigInt.parse(t.substring(2), radix: 16);
      } catch (e) {
        throw FormatException('Balance response toString() is invalid hex: "$t" - $e');
      }
    }
    try {
      return BigInt.parse(t);
    } catch (e) {
      throw FormatException(
        'Balance response cannot be parsed. Type: ${v.runtimeType}, '
        'Value: "${t.substring(0, 100)}" - $e'
      );
    }
  }

  int _parseInt(dynamic v) {
    if (v == null) return 0;
    if (v is int) return v;
    if (v is String) {
      final s = v.trim();
      if (s.startsWith('0x') || s.startsWith('0X')) {
        if (s.length <= 2) return 0;
        return int.parse(s.substring(2), radix: 16);
      }
      return int.tryParse(s) ?? int.parse(json.decode(s).toString());
    }
    return int.parse(v.toString());
  }

  /// Dispose underlying HTTP client if you created many instances.
  void close() => rpc.close();
}
