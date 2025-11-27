/*
 * Animica Wallet — Transaction Builders
 *
 * Builders for common flows:
 *  • transfer()                      — native ANM transfer
 *  • callRaw()                       — contract call with raw calldata
 *  • callAbi()                       — contract call using minimal ABI + CBOR args
 *  • deployRaw()                     — contract deployment with raw init code
 *  • deployWithConstructorCbor()     — deployment with CBOR-encoded constructor args
 *
 * Notes
 *  - Addresses must be bech32m "am1..." (AnimicaAddr validator).
 *  - ABI: We use a minimal scheme where call data = selector(4 bytes) || CBOR(args).
 *         The selector is keccak256("name(types)")[0..4).
 *  - CBOR encoding is deterministic (see codec/cbor.dart).
 */

import 'dart:typed_data';

import '../crypto/bech32m.dart' show AnimicaAddr;
import '../codec/cbor.dart' show Cbor;
import '../abi/abi.dart';
import 'tx_types.dart';

class TxBuilder {
  // Reasonable defaults; callers should override with real estimates.
  static final BigInt defaultTransferGas = BigInt.from(21000);
  static final BigInt defaultCallGas = BigInt.from(200000);
  static final BigInt defaultDeployGas = BigInt.from(800000);

  /// Simple helper for a legacy-fee default (e.g., 1 gwei-equivalent).
  static FeeParams defaultLegacyFee({BigInt? gasPrice}) =>
      FeeParams.legacy(gasPrice: gasPrice ?? BigInt.from(1000000000));

  /// Simple helper for an EIP-1559 style default.
  static FeeParams defaultEip1559({
    BigInt? maxFeePerGas,
    BigInt? maxPriorityFeePerGas,
  }) =>
      FeeParams.eip1559(
        maxFeePerGas: maxFeePerGas ?? BigInt.from(2000000000),
        maxPriorityFeePerGas: maxPriorityFeePerGas ?? BigInt.from(250000000),
      );

  /// Build a native-value transfer.
  static Tx transfer({
    required int chainId,
    required BigInt nonce,
    required String to,
    required BigInt value,
    BigInt? gasLimit,
    FeeParams? fee,
  }) {
    _requireAddress(to);
    return Tx(
      chainId: chainId,
      nonce: nonce,
      to: to,
      value: value,
      data: Uint8List(0),
      gasLimit: gasLimit ?? defaultTransferGas,
      fee: fee ?? defaultLegacyFee(),
      kind: TxKind.transfer,
    );
  }

  /// Contract call with raw [data] (already encoded).
  static Tx callRaw({
    required int chainId,
    required BigInt nonce,
    required String to,
    Uint8List? data,
    BigInt? value,
    BigInt? gasLimit,
    FeeParams? fee,
  }) {
    _requireAddress(to);
    return Tx(
      chainId: chainId,
      nonce: nonce,
      to: to,
      value: value ?? BigInt.zero,
      data: data ?? Uint8List(0),
      gasLimit: gasLimit ?? defaultCallGas,
      fee: fee ?? defaultLegacyFee(),
      kind: TxKind.call,
    );
  }

  /// Contract call using minimal ABI + deterministic CBOR argument list.
  ///
  /// Encoding: calldata = selector4 || CBOR.encode(args)
  /// Where selector4 = keccak256("name(type,...)")[0..4)
  static Tx callAbi({
    required int chainId,
    required BigInt nonce,
    required String to,
    required FunctionAbi fn,
    required List<Object?> args,
    BigInt? value,
    BigInt? gasLimit,
    FeeParams? fee,
  }) {
    _requireAddress(to);
    _validateArityAndTypes(fn, args);
    final sel = fn.selector4();
    final body = Cbor.encode(args);
    final data = _concat(sel, body);
    return Tx(
      chainId: chainId,
      nonce: nonce,
      to: to,
      value: value ?? BigInt.zero,
      data: data,
      gasLimit: gasLimit ?? defaultCallGas,
      fee: fee ?? defaultLegacyFee(),
      kind: TxKind.call,
    );
  }

  /// Raw contract deployment with prebuilt init code in [bytecode].
  static Tx deployRaw({
    required int chainId,
    required BigInt nonce,
    required Uint8List bytecode,
    BigInt? value,
    BigInt? gasLimit,
    FeeParams? fee,
  }) {
    return Tx(
      chainId: chainId,
      nonce: nonce,
      to: null, // deploy
      value: value ?? BigInt.zero,
      data: bytecode,
      gasLimit: gasLimit ?? defaultDeployGas,
      fee: fee ?? defaultLegacyFee(),
      kind: TxKind.deploy,
    );
  }

  /// Deployment with constructor args (CBOR-encoded) appended to [bytecode].
  ///
  /// For Animica VM packages, constructors are typically positional args with
  /// types described by [constructorInputs]. We validate each arg against the
  /// declared type and append CBOR(args) to the end of the init code.
  static Tx deployWithConstructorCbor({
    required int chainId,
    required BigInt nonce,
    required Uint8List bytecode,
    required List<AbiParam> constructorInputs,
    required List<Object?> args,
    BigInt? value,
    BigInt? gasLimit,
    FeeParams? fee,
  }) {
    _validateConstructorTypes(constructorInputs, args);
    final encodedArgs = Cbor.encode(args);
    final init = _concat(bytecode, encodedArgs);
    return deployRaw(
      chainId: chainId,
      nonce: nonce,
      bytecode: init,
      value: value ?? BigInt.zero,
      gasLimit: gasLimit ?? defaultDeployGas,
      fee: fee ?? defaultLegacyFee(),
    );
  }

  // ---------------------------------------------------------------------------
  // Validation helpers
  // ---------------------------------------------------------------------------

  static void _requireAddress(String a) {
    if (!AnimicaAddr.isValid(a)) {
      throw ArgumentError('Invalid Animica bech32m address: $a');
    }
  }

  static void _validateArityAndTypes(FunctionAbi fn, List<Object?> args) {
    if (args.length != fn.inputs.length) {
      throw ArgumentError(
          'Argument count mismatch for ${fn.name}: expected ${fn.inputs.length}, got ${args.length}');
    }
    for (var i = 0; i < args.length; i++) {
      final t = fn.inputs[i].type;
      final v = args[i];
      if (!t.accepts(v)) {
        throw ArgumentError('Arg $i (${fn.inputs[i].name}) not accepted by type ${t.canonical}');
      }
    }
  }

  static void _validateConstructorTypes(List<AbiParam> inputs, List<Object?> args) {
    if (args.length != inputs.length) {
      throw ArgumentError(
          'Constructor arg count mismatch: expected ${inputs.length}, got ${args.length}');
    }
    for (var i = 0; i < args.length; i++) {
      final t = inputs[i].type;
      final v = args[i];
      if (!t.accepts(v)) {
        throw ArgumentError('Constructor arg $i (${inputs[i].name}) fails type ${t.canonical}');
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Bytes helpers
  // ---------------------------------------------------------------------------

  static Uint8List _concat(Uint8List a, Uint8List b) {
    final out = Uint8List(a.length + b.length);
    out.setAll(0, a);
    out.setAll(a.length, b);
    return out;
  }
}
