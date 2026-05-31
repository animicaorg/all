// Build, sign, and broadcast Animica transactions.
//
// Three tx flavors:
//
//   transfer (kind=0): data = empty bytes, value > 0 if you want to move ANM
//   call     (kind=2): data = pre-encoded calldata bytes (no `code`/`manifest`)
//   deploy   (kind=1): contains `code` + `manifest` instead of `data`
//
// The chain's tx builder infers the kind from the body's contents — we
// just write the right fields in.

import 'dart:typed_data';

import '../constants.dart';
import '../models/account.dart';
import 'canonical.dart';
import 'keys.dart';
import 'ml_dsa_65.dart';
import 'rpc.dart';

class SignedTx {
  final Uint8List rawTx;
  final Uint8List bodyCbor;
  final Uint8List signature;
  final int nonce;
  const SignedTx({
    required this.rawTx,
    required this.bodyCbor,
    required this.signature,
    required this.nonce,
  });
}

/// Build a transfer body (kind=0). Amount is in nanos (1 ANM = 1e9 nanos).
Map<String, dynamic> buildTransferBody({
  required String from,
  required String to,
  required BigInt amountNanos,
  required int nonce,
  int gasLimit = 21000,
  int maxFee = 1000000000,
  int chainId = 1,
}) {
  return <String, dynamic>{
    'to': to,
    'data': Uint8List(0),
    'from': from,
    'nonce': nonce,
    'value': amountNanos,
    'maxFee': maxFee,
    'chainId': chainId,
    'gasLimit': gasLimit,
  };
}

/// Build a contract-call body (kind=2). `calldata` is the pre-encoded
/// bytes returned by your encoder of choice (server-side encode-call,
/// or a known constant like the founders mint selector).
Map<String, dynamic> buildCallBody({
  required String from,
  required String to,
  required Uint8List calldata,
  required int nonce,
  BigInt? value,
  int gasLimit = 200000,
  int maxFee = 1000000000,
  int chainId = 1,
}) {
  return <String, dynamic>{
    'to': to,
    'data': calldata,
    'from': from,
    'nonce': nonce,
    'value': value ?? BigInt.zero,
    'maxFee': maxFee,
    'chainId': chainId,
    'gasLimit': gasLimit,
  };
}

/// Sign a tx body with `account`. Returns the raw CBOR envelope ready
/// for `tx.sendRawTransaction`. Dispatches by `account.algId`:
///   0x1003 → ML-DSA-65 (real FIPS 204, chain v2 default — async)
///   0x1002 → SPHINCS-SHAKE-128s (deprecated commitment stub)
///   0x1001 → Dilithium3 (deprecated commitment stub)
///
/// Returns a Future because the ML-DSA-65 path runs through flutter_js
/// which is async-only. The legacy stub paths complete synchronously,
/// just wrapped in a resolved Future for API uniformity.
Future<SignedTx> signTx({
  required Account account,
  required Map<String, dynamic> body,
}) async {
  final bodyCbor = canonicalCbor(body);
  final prehash = buildSignBytes(
    msg: bodyCbor,
    algId: account.algId,
    chainId: body['chainId'] is int ? body['chainId'] as int : null,
  );

  final Uint8List sig;
  switch (account.algId) {
    case AnimicaConfig.algIdMlDsa65:
      sig = await MlDsa65.sign(account.secretKey, prehash);
      break;
    case AnimicaConfig.algIdSphincs:
      sig = signSphincs(account.publicKey, prehash);
      break;
    case AnimicaConfig.algIdDilithium3:
      sig = signDilithium3(
        sk: account.secretKey,
        prehash: prehash,
        pk: account.publicKey,
      );
      break;
    default:
      throw UnsupportedError(
        'Unknown alg_id 0x${account.algId.toRadixString(16)} — '
        'supported: ML-DSA-65 (0x1003), SPHINCS-128s (0x1002), Dilithium3 (0x1001).',
      );
  }

  final raw = packSignedEnvelope(
    body: body,
    algId: account.algId,
    publicKey: account.publicKey,
    signature: sig,
  );
  return SignedTx(
    rawTx: raw,
    bodyCbor: bodyCbor,
    signature: sig,
    nonce: body['nonce'] as int,
  );
}

/// Sign + broadcast in one shot. Returns the tx hash returned by the
/// node.
Future<String> signAndBroadcast({
  required RpcClient rpc,
  required Account account,
  required Map<String, dynamic> body,
}) async {
  final signed = await signTx(account: account, body: body);
  return rpc.sendRawTransaction(signed.rawTx);
}
