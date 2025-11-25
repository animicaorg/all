/*
 * Animica Wallet — TxService
 *
 * Provides:
 *   • sendRawTransaction(Uint8List raw) → String txHash (0x…)
 *   • waitForReceipt(String txHash, {confirmations, timeout, pollEvery}) → TxReceipt
 *
 * JSON-RPC compatibility:
 *   - Default uses Animica-style method names (animica_*).
 *   - Flip _USE_ETH_NAMES to true if your node exposes Ethereum names.
 *
 * Optional ideas for later:
 *   - WS-assisted confirmation (subscribe to newHeads) for faster reactions.
 *   - EIP-1559 style gas hints (if/when supported by Animica).
 */

import 'dart:async';
import 'dart:typed_data';
import 'dart:convert' show json;

import 'env.dart';
import 'rpc_client.dart';
import 'state_service.dart';
import '../tx/tx_types.dart' show TxReceipt;

const bool _USE_ETH_NAMES = false;

final _M_SEND_RAW_TX  = _USE_ETH_NAMES ? 'eth_sendRawTransaction'        : 'animica_sendRawTransaction';
final _M_GET_RECEIPT  = _USE_ETH_NAMES ? 'eth_getTransactionReceipt'     : 'animica_getTransactionReceipt';
final _M_BLOCK_NUMBER = _USE_ETH_NAMES ? 'eth_blockNumber'               : 'animica_blockNumber';

class TxService {
  final RpcClient rpc;
  final StateService _state;

  TxService(this.rpc) : _state = StateService(rpc);

  factory TxService.fromEnv() => TxService(RpcClient.fromEnv());

  /// Broadcast a signed transaction. [raw] is the RLP/CBOR/etc-encoded bytes
  /// expected by the node, already signed. Returns tx hash (0x…).
  Future<String> sendRawTransaction(Uint8List raw) async {
    final hex = _hex0x(raw);
    final res = await rpc.call<dynamic>(_M_SEND_RAW_TX, [hex]);
    if (res is String) return res;
    // Some nodes may return { "hash": "0x…" }
    if (res is Map && res['hash'] is String) return res['hash'] as String;
    throw RpcException(
      method: _M_SEND_RAW_TX,
      endpoint: rpc.endpoint,
      message: 'Unexpected sendRawTransaction response: ${json.encode(res)}',
    );
  }

  /// Wait for a transaction receipt to be available and (optionally) reach
  /// [confirmations] blocks. Returns the parsed [TxReceipt].
  ///
  /// If the receipt never appears before [timeout], throws [TimeoutException].
  Future<TxReceipt> waitForReceipt(
    String txHash, {
    int confirmations = 1,
    Duration timeout = const Duration(minutes: 2),
    Duration pollEvery = const Duration(seconds: 2),
    void Function(int pollCount, int? head, TxReceipt? r)? onPoll,
  }) async {
    final deadline = DateTime.now().add(timeout);
    int polls = 0;

    // First: wait until receipt exists
    TxReceipt receipt = await _waitUntil(
      () async {
        polls++;
        final r = await _getReceipt(txHash);
        final head = await _maybeHead();
        onPoll?.call(polls, head, r);
        return r;
      },
      (r) => r != null,
      timeout: timeout,
      pollEvery: pollEvery,
    ) as TxReceipt;

    // Then: wait for confirmations (if requested)
    if (confirmations > 1) {
      while (true) {
        final now = DateTime.now();
        if (now.isAfter(deadline)) {
          throw TimeoutException('Timed out waiting for $confirmations confirmations');
        }
        final head = await _head();
        final bn = _asInt(receipt.blockNumber);
        final conf = (head - bn) + 1; // inclusive of block containing tx
        onPoll?.call(++polls, head, receipt);
        if (conf >= confirmations) break;
        await Future.delayed(pollEvery);
        // Optionally refresh receipt (in case of reorg fields change)
        final latest = await _getReceipt(txHash);
        if (latest != null) receipt = latest;
      }
    }

    return receipt;
  }

  // ---------------- Internals ----------------

  Future<TxReceipt?> _getReceipt(String txHash) async {
    final res = await rpc.call<dynamic>(_M_GET_RECEIPT, [txHash]);
    if (res == null) return null;
    if (res is Map<String, dynamic>) {
      return TxReceipt.fromJson(res);
    }
    if (res is Map) {
      return TxReceipt.fromJson(res.map((k, v) => MapEntry(k.toString(), v)));
    }
    // Some nodes may wrap: { "receipt": {..} }
    if (res is Map && res['receipt'] is Map) {
      final m = (res['receipt'] as Map).map((k, v) => MapEntry(k.toString(), v));
      return TxReceipt.fromJson(m);
    }
    throw RpcException(
      method: _M_GET_RECEIPT,
      endpoint: rpc.endpoint,
      message: 'Unexpected getTransactionReceipt response: ${json.encode(res)}',
    );
  }

  Future<int> _maybeHead() async {
    try {
      return await _head();
    } catch (_) {
      return -1;
    }
  }

  Future<int> _head() async {
    final res = await rpc.call<dynamic>(_M_BLOCK_NUMBER);
    return _asInt(res);
  }

  Future<T> _waitUntil<T>(
    Future<T> Function() producer,
    bool Function(T) predicate, {
    required Duration timeout,
    required Duration pollEvery,
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (true) {
      final v = await producer();
      if (predicate(v)) return v;
      if (DateTime.now().isAfter(deadline)) {
        throw TimeoutException('Timed out waiting for condition');
      }
      await Future.delayed(pollEvery);
    }
  }

  static String _hex0x(Uint8List bytes) {
    final sb = StringBuffer('0x');
    for (final b in bytes) {
      if (b < 16) {
        sb.write('0');
      }
      sb.write(b.toRadixString(16));
    }
    return sb.toString();
  }

  /// Parse either 0xHEX or decimal-ish dynamic into int.
  static int _asInt(dynamic v) {
    if (v == null) return 0;
    if (v is int) return v;
    final s = v.toString().trim();
    if (s.startsWith('0x') || s.startsWith('0X')) {
      return s.length <= 2 ? 0 : int.parse(s.substring(2), radix: 16);
    }
    return int.tryParse(s) ?? int.parse(json.decode(s).toString());
  }

  /// Dispose underlying HTTP client if created via fromEnv().
  void close() => rpc.close();
}
