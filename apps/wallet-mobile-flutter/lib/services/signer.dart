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
/// for `tx.sendRawTransaction`.
SignedTx signTx({
  required Account account,
  required Map<String, dynamic> body,
}) {
  if (account.algId != AnimicaConfig.algIdSphincs) {
    throw UnsupportedError(
      'Mobile wallet currently only signs SPHINCS-128s (alg 0x1002) txs. '
      'Dilithium3 needs ML-DSA-65 Dart port — v0.3 follow-up.',
    );
  }
  final bodyCbor = canonicalCbor(body);
  final prehash = buildSignBytes(
    msg: bodyCbor,
    algId: account.algId,
    chainId: body['chainId'] is int ? body['chainId'] as int : null,
  );
  final sig = signSphincs(account.publicKey, prehash);
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
  final signed = signTx(account: account, body: body);
  return rpc.sendRawTransaction(signed.rawTx);
}
