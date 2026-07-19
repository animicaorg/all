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

/// Max size of the optional `data` payload on a transfer. Mirrors the node's
/// mempool guardrail (mempool/validate.py) so the wallet fails fast locally
/// instead of building a tx the network will reject. Store purchase memos
/// (ANMSTORE1) are always well under this (<=300 bytes).
const int kMaxTransferDataBytes = 1024;

/// Build a transfer body (kind=0). Amount is in nanos (1 ANM = 1e9 nanos).
///
/// `data` is an optional opaque payload written verbatim into the body's
/// `data` bstr field — used for on-chain memos like the App Store's
/// `ANMSTORE1` purchase memo. When omitted (the default) the body is
/// byte-for-byte identical to the historical empty-data transfer, so every
/// existing golden vector and broadcast envelope is unchanged. The field
/// name, position and CBOR encoding match the Python builder exactly
/// (omni_sdk `make_tx` / `build_signable_tx_bytes`), which the chain and the
/// e2e pay-intent helper (`scripts/e2e_pay_intent.py`) sign over.
Map<String, dynamic> buildTransferBody({
  required String from,
  required String to,
  required BigInt amountNanos,
  required int nonce,
  Uint8List? data,
  int gasLimit = 21000,
  int maxFee = 1000000000,
  int chainId = 1,
}) {
  if (data != null && data.length > kMaxTransferDataBytes) {
    throw ArgumentError(
      'transfer data too large: ${data.length} bytes > '
      '$kMaxTransferDataBytes-byte limit',
    );
  }
  return <String, dynamic>{
    'to': to,
    'data': data ?? Uint8List(0),
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
    case AnimicaConfig.algIdDilithium3:
      // Legacy stub schemes (sphincs_shake_128s 0x1002 / dilithium3 0x1001) are
      // forgeable and rejected by the node — any signature they produce can
      // never be mined. Refuse rather than build an unspendable transaction.
      throw UnsupportedError(
        'This is a legacy '
        '${account.algId == AnimicaConfig.algIdDilithium3 ? "Dilithium3" : "SPHINCS-128s"} '
        'wallet (alg_id 0x${account.algId.toRadixString(16)}). The network no longer '
        'accepts this scheme, so its balance cannot be sent. Move funds via an '
        'ML-DSA-65 wallet.',
      );
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
