/*
 * Animica Wallet — Transaction & Receipt Dataclasses
 *
 * These plain data models are UI-/RPC-friendly and intentionally
 * encoding-agnostic. Bytes are represented as Uint8List in-memory and
 * hex strings (0x…) in JSON.
 *
 * Related files:
 *   • tx_builder.dart     — convenience builders for transfer/call/deploy
 *   • tx_signbytes.dart   — canonical SignBytes domain encoder
 */

import 'dart:typed_data';
import 'dart:convert' show json;

/// ---------------------------------------------------------------------------
/// Enums & simple types
/// ---------------------------------------------------------------------------

/// High-level intent of a transaction.
enum TxKind { transfer, call, deploy }

/// Fee model. Animica can support legacy (flat gasPrice) or EIP-1559 style.
enum FeeModel { legacy, eip1559 }

/// Supported signature algorithms (must match chain policy).
enum SigAlgo { ed25519, secp256k1, dilithium3, sphincsPlus }

/// Execution result.
enum ReceiptStatus { success, reverted }

/// ---------------------------------------------------------------------------
/// Fee parameters
/// ---------------------------------------------------------------------------

class FeeParams {
  final FeeModel model;

  /// For FeeModel.legacy
  final BigInt? gasPrice; // price per gas unit

  /// For FeeModel.eip1559
  final BigInt? maxFeePerGas;
  final BigInt? maxPriorityFeePerGas;

  const FeeParams._({
    required this.model,
    this.gasPrice,
    this.maxFeePerGas,
    this.maxPriorityFeePerGas,
  });

  factory FeeParams.legacy({required BigInt gasPrice}) =>
      FeeParams._(model: FeeModel.legacy, gasPrice: gasPrice);

  factory FeeParams.eip1559({
    required BigInt maxFeePerGas,
    required BigInt maxPriorityFeePerGas,
  }) =>
      FeeParams._(
        model: FeeModel.eip1559,
        maxFeePerGas: maxFeePerGas,
        maxPriorityFeePerGas: maxPriorityFeePerGas,
      );

  Map<String, Object?> toJson() => switch (model) {
        FeeModel.legacy => {
            'model': 'legacy',
            'gasPrice': _hex(gasPrice ?? BigInt.zero),
          },
        FeeModel.eip1559 => {
            'model': 'eip1559',
            'maxFeePerGas': _hex(maxFeePerGas ?? BigInt.zero),
            'maxPriorityFeePerGas': _hex(maxPriorityFeePerGas ?? BigInt.zero),
          },
      };

  factory FeeParams.fromJson(Map<String, Object?> j) {
    final model = (j['model'] as String? ?? 'legacy').toLowerCase();
    if (model == 'eip1559') {
      return FeeParams.eip1559(
        maxFeePerGas: _big(j['maxFeePerGas']),
        maxPriorityFeePerGas: _big(j['maxPriorityFeePerGas']),
      );
    }
    return FeeParams.legacy(gasPrice: _big(j['gasPrice']));
  }
}

/// ---------------------------------------------------------------------------
/// Transaction structures
/// ---------------------------------------------------------------------------

class Tx {
  /// Chain ID (e.g., 1 mainnet, 2 testnet, 1337 localnet).
  final int chainId;

  /// Monotonic per-sender.
  final BigInt nonce;

  /// Destination account or null for contract deployment.
  final String? to; // bech32m am1… address

  /// Native value (atto-ANM).
  final BigInt value;

  /// Calldata / init code.
  final Uint8List data;

  /// Gas limit for execution.
  final BigInt gasLimit;

  /// Fee model & params.
  final FeeParams fee;

  /// Optional metadata/versioning
  final TxKind kind;

  const Tx({
    required this.chainId,
    required this.nonce,
    required this.to,
    required this.value,
    required this.data,
    required this.gasLimit,
    required this.fee,
    this.kind = TxKind.call,
  });

  Tx copyWith({
    int? chainId,
    BigInt? nonce,
    String? to,
    BigInt? value,
    Uint8List? data,
    BigInt? gasLimit,
    FeeParams? fee,
    TxKind? kind,
  }) {
    return Tx(
      chainId: chainId ?? this.chainId,
      nonce: nonce ?? this.nonce,
      to: to ?? this.to,
      value: value ?? this.value,
      data: data ?? this.data,
      gasLimit: gasLimit ?? this.gasLimit,
      fee: fee ?? this.fee,
      kind: kind ?? this.kind,
    );
  }

  Map<String, Object?> toJson() => {
        'chainId': chainId,
        'nonce': _hex(nonce),
        'to': to,
        'value': _hex(value),
        'data': _hexBytes(data),
        'gasLimit': _hex(gasLimit),
        'fee': fee.toJson(),
        'kind': kind.name,
      };

  factory Tx.fromJson(Map<String, Object?> j) => Tx(
        chainId: (j['chainId'] as num).toInt(),
        nonce: _big(j['nonce']),
        to: j['to'] as String?,
        value: _big(j['value']),
        data: _bytes(j['data']),
        gasLimit: _big(j['gasLimit']),
        fee: FeeParams.fromJson(Map<String, Object?>.from(j['fee'] as Map)),
        kind: _parseKind(j['kind'] as String?),
      );
}

class Signature {
  final SigAlgo algo;
  final Uint8List pubkey;    // raw key bytes
  final Uint8List signature; // raw sig bytes

  const Signature({
    required this.algo,
    required this.pubkey,
    required this.signature,
  });

  Map<String, Object?> toJson() => {
        'algo': algo.name,
        'pubkey': _hexBytes(pubkey),
        'signature': _hexBytes(signature),
      };

  factory Signature.fromJson(Map<String, Object?> j) => Signature(
        algo: _parseAlgo(j['algo'] as String?),
        pubkey: _bytes(j['pubkey']),
        signature: _bytes(j['signature']),
      );
}

class SignedTx {
  final Tx tx;
  final Signature sig;

  /// Hash of the encoded signed transaction (0x…).
  final String? hash;

  const SignedTx({required this.tx, required this.sig, this.hash});

  Map<String, Object?> toJson() => {
        'tx': tx.toJson(),
        'sig': sig.toJson(),
        if (hash != null) 'hash': hash,
      };

  factory SignedTx.fromJson(Map<String, Object?> j) => SignedTx(
        tx: Tx.fromJson(Map<String, Object?>.from(j['tx'] as Map)),
        sig: Signature.fromJson(Map<String, Object?>.from(j['sig'] as Map)),
        hash: j['hash'] as String?,
      );
}

/// ---------------------------------------------------------------------------
/// Receipt & logs
/// ---------------------------------------------------------------------------

class LogEntry {
  final String address;          // emitter
  final List<String> topics;     // 0x… 32-byte topics
  final Uint8List data;          // raw data
  final int? index;              // position within tx receipt

  const LogEntry({
    required this.address,
    required this.topics,
    required this.data,
    this.index,
  });

  Map<String, Object?> toJson() => {
        'address': address,
        'topics': topics,
        'data': _hexBytes(data),
        if (index != null) 'index': index,
      };

  factory LogEntry.fromJson(Map<String, Object?> j) => LogEntry(
        address: j['address'] as String,
        topics: ((j['topics'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        data: _bytes(j['data']),
        index: (j['index'] as num?)?.toInt(),
      );
}

class TxReceipt {
  final String txHash;                 // 0x…
  final ReceiptStatus status;
  final int? blockNumber;
  final String? blockHash;             // 0x…
  final int? txIndex;

  final BigInt gasUsed;
  final BigInt? cumulativeGasUsed;

  /// Address of created contract for deploy txs.
  final String? contractAddress;

  /// Return data from call.
  final Uint8List? returnData;

  final List<LogEntry> logs;

  /// Optional timestamp (seconds since epoch).
  final int? timestamp;

  const TxReceipt({
    required this.txHash,
    required this.status,
    required this.gasUsed,
    this.blockNumber,
    this.blockHash,
    this.txIndex,
    this.cumulativeGasUsed,
    this.contractAddress,
    this.returnData,
    this.logs = const [],
    this.timestamp,
  });

  Map<String, Object?> toJson() => {
        'txHash': txHash,
        'status': status.name,
        if (blockNumber != null) 'blockNumber': blockNumber,
        if (blockHash != null) 'blockHash': blockHash,
        if (txIndex != null) 'txIndex': txIndex,
        'gasUsed': _hex(gasUsed),
        if (cumulativeGasUsed != null)
          'cumulativeGasUsed': _hex(cumulativeGasUsed!),
        if (contractAddress != null) 'contractAddress': contractAddress,
        if (returnData != null) 'returnData': _hexBytes(returnData!),
        'logs': logs.map((l) => l.toJson()).toList(),
        if (timestamp != null) 'timestamp': timestamp,
      };

  factory TxReceipt.fromJson(Map<String, Object?> j) => TxReceipt(
        txHash: j['txHash'] as String,
        status: _parseStatus(j['status'] as String?),
        blockNumber: (j['blockNumber'] as num?)?.toInt(),
        blockHash: j['blockHash'] as String?,
        txIndex: (j['txIndex'] as num?)?.toInt(),
        gasUsed: _big(j['gasUsed']),
        cumulativeGasUsed:
            j['cumulativeGasUsed'] != null ? _big(j['cumulativeGasUsed']) : null,
        contractAddress: j['contractAddress'] as String?,
        returnData: j['returnData'] != null ? _bytes(j['returnData']) : null,
        logs: ((j['logs'] as List?) ?? const [])
            .map((e) => LogEntry.fromJson(Map<String, Object?>.from(e as Map)))
            .toList(),
        timestamp: (j['timestamp'] as num?)?.toInt(),
      );
}

/// ---------------------------------------------------------------------------
/// Helpers (hex ↔ bytes, bigints)
/// ---------------------------------------------------------------------------

String _hex(BigInt v) {
  final n = v < BigInt.zero ? -v : v;
  final hex = n.toRadixString(16);
  final even = hex.length.isOdd ? '0$hex' : hex;
  final pref = v < BigInt.zero ? '-' : '';
  return '${pref}0x$even';
}

String _hexBytes(Uint8List bs) {
  const hexd = '0123456789abcdef';
  final out = StringBuffer('0x');
  for (final b in bs) {
    out.write(hexd[(b >> 4) & 0xF]);
    out.write(hexd[b & 0xF]);
  }
  return out.toString();
}

Uint8List _bytes(Object? x) {
  if (x is Uint8List) return x;
  final s = x?.toString() ?? '';
  if (s.startsWith('0x') || s.startsWith('0X')) {
    final h = s.substring(2);
    final even = h.length.isOdd ? '0$h' : h;
    final out = Uint8List(even.length ~/ 2);
    for (var i = 0; i < even.length; i += 2) {
      out[i ~/ 2] = int.parse(even.substring(i, i + 2), radix: 16);
    }
    return out;
  }
  throw ArgumentError('Expected hex 0x… for bytes');
}

BigInt _big(Object? x) {
  if (x == null) return BigInt.zero;
  if (x is BigInt) return x;
  if (x is num) return BigInt.from(x);
  final s = x.toString();
  if (s.startsWith('-0x') || s.startsWith('-0X')) {
    return -_big('0x${s.substring(3)}');
  }
  if (s.startsWith('0x') || s.startsWith('0X')) {
    final h = s.substring(2);
    if (h.isEmpty) return BigInt.zero;
    return BigInt.parse(h, radix: 16);
  }
  return BigInt.parse(s);
}

TxKind _parseKind(String? s) {
  switch ((s ?? '').toLowerCase()) {
    case 'transfer':
      return TxKind.transfer;
    case 'deploy':
      return TxKind.deploy;
    case 'call':
    default:
      return TxKind.call;
  }
}

SigAlgo _parseAlgo(String? s) {
  switch ((s ?? '').toLowerCase()) {
    case 'ed25519':
      return SigAlgo.ed25519;
    case 'secp256k1':
      return SigAlgo.secp256k1;
    case 'dilithium3':
      return SigAlgo.dilithium3;
    case 'sphincsplus':
    case 'sphincs+':
      return SigAlgo.sphincsPlus;
    default:
      return SigAlgo.dilithium3; // default to PQ-safe
  }
}

ReceiptStatus _parseStatus(String? s) {
  switch ((s ?? '').toLowerCase()) {
    case 'success':
    case 'ok':
      return ReceiptStatus.success;
    case 'reverted':
    case 'fail':
    case 'failed':
      return ReceiptStatus.reverted;
    default:
      return ReceiptStatus.reverted;
  }
}
