/*
 * Animica Wallet — SignBytes domain encoder
 *
 * Canonical, deterministic CBOR sign-bytes for transactions.
 *
 * Design:
 *  • Domain-separated with a version tag: "animica.tx.v1"
 *  • Chain replay protection: chainId is embedded
 *  • Deterministic CBOR arrays (no maps) to avoid key ordering concerns
 *  • BigInts are encoded per RFC 8949 (our CBOR handles tag 2/3 when needed)
 *  • Addresses are bech32m strings ("am1…"); deploy uses null for `to`
 *
 * Encoding (CBOR array):
 * [
 *   "animica.tx.v1",
 *   chainId,
 *   [
 *     kind,                 // 0=transfer, 1=call, 2=deploy
 *     nonce,                // BigInt
 *     to|null,              // bech32m string or null (deploy)
 *     value,                // BigInt (atto-ANM)
 *     data,                 // bytes (Uint8List)
 *     gasLimit,             // BigInt
 *     [                     // fee
 *       model,              // 0=legacy, 1=eip1559
 *       ...payload          // legacy: [gasPrice]; eip1559: [maxFeePerGas, maxPriorityFeePerGas]
 *     ]
 *   ]
 * ]
 *
 * Hashing: keccak256(encode(tx))
 */

import 'dart:typed_data';
import '../codec/cbor.dart' show Cbor;
import '../crypto/sha3.dart' as sha3;
import '../crypto/bech32m.dart' show AnimicaAddr;
import 'tx_types.dart';

class TxSignBytes {
  static const String _domain = 'animica.tx.v1';

  /// Build canonical sign-bytes (CBOR) for [tx].
  static Uint8List encode(Tx tx) {
    // Basic validation to avoid signing malformed content.
    if (tx.to != null && !AnimicaAddr.isValid(tx.to!)) {
      throw ArgumentError('Invalid Animica address in Tx.to: ${tx.to}');
    }
    final kind = switch (tx.kind) {
      TxKind.transfer => 0,
      TxKind.call => 1,
      TxKind.deploy => 2,
    };

    final feeTuple = switch (tx.fee.model) {
      FeeModel.legacy => [
          0, // model
          tx.fee.gasPrice ?? BigInt.zero,
        ],
      FeeModel.eip1559 => [
          1, // model
          tx.fee.maxFeePerGas ?? BigInt.zero,
          tx.fee.maxPriorityFeePerGas ?? BigInt.zero,
        ],
    };

    final core = [
      kind,
      tx.nonce,
      tx.to, // may be null for deploy
      tx.value,
      tx.data,
      tx.gasLimit,
      feeTuple,
    ];

    final top = [
      _domain,
      tx.chainId,
      core,
    ];

    return Cbor.encode(top);
  }

  /// keccak256(sign-bytes)
  static Uint8List hash(Uint8List signBytes) => sha3.keccak256(signBytes);

  /// Convenience: compute keccak256(sign-bytes) directly from [tx].
  static Uint8List hashTx(Tx tx) => hash(encode(tx));

  /// Hex helpers
  static String toHex(Uint8List bs, {bool with0x = true}) =>
      Cbor.toHex(bs, with0x: with0x);
}
