// Build, sign, and broadcast Animica transactions.
//
// Three tx flavors, all emitted in the chain's CANONICAL nested body shape
// (`{v, chainId, from, gas:{price,limit}, payload:{t,v}, accessList, nonce}`):
//
//   transfer (t=0): payload.v = {to, amount, data}   (data may be empty)
//   deploy   (t=1): payload.v = {code, manifest}
//   call     (t=2): payload.v = {to, data}           (data must be non-empty)
//
// Why canonical and not the old flat `{to,data,from,nonce,…}` map: the node
// does NOT verify a signature over the body it was handed. It rebuilds
// `tx_signing_preimage` (python/animica/tx/signing.py) and puts
// `normalize_tx_body(body)` — NOT the body — at key 7. `normalize_tx_body`
// (core/utils/tx.py) is an identity passthrough only when `v` + `gas` +
// `payload` are all present; anything else it REWRITES, with a hardcoded
// `payload.t = 0`. So a flat body produced two failures at once:
//
//   1. a signature over the flat body (or over a preimage wrapping the flat
//      body) never matches the preimage the node reconstructs → BadSignature
//      on every transfer, call, store purchase and dapp send; and
//   2. `buildCallBody`'s contract calls were silently rewritten into plain
//      transfers, because t=1/t=2 are unreachable from a flat body.
//
// Emitting canonical bodies makes `normalize_tx_body` a no-op, which makes the
// wallet's preimage byte-identical to the node's. See
// test/tx_golden_vectors_test.dart, whose vectors come from the node's own
// Python (`core.utils.tx.normalize_tx_body`, `animica.tx.signing.
// tx_signing_preimage`, `core.encoding.cbor.dumps`).
//
// Layout mirrors `toCanonicalBodyShape` in
// apps/wallet-extension/src/tx/encode.ts and `UnsignedTx.to_obj` in
// core/types/tx.py, including addresses as the raw 32-byte bech32m digest
// (what the node's `_pad_addr` produces).

import 'dart:typed_data';

import '../constants.dart';
import '../models/account.dart';
import 'address.dart';
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

/// Gas price (nanos per gas unit) the wallet offers by default.
const int kDefaultGasPrice = 1000000000;

/// Default gas limit for a plain transfer.
const int kDefaultTransferGasLimit = 21000;

/// Default gas limit for a contract call.
const int kDefaultCallGasLimit = 200000;

/// Default gas limit for a deploy. A deploy carries contract bytecode plus a
/// manifest, so the 21k transfer default underprices even a tiny token deploy
/// and the tx runs out of gas mid-init. Matches the extension's
/// `defaultGasLimitForKind` (apps/wallet-extension/src/background/index.ts).
const int kDefaultDeployGasLimit = 2000000;

/// Cap on `code.length + manifest.length`.
///
/// Mainnet's `tx_max_bytes` is 131_072 (core/genesis/spec/params.yaml) and the
/// signed envelope adds the ML-DSA-65 pubkey (1952 B) + signature (3309 B) +
/// CBOR framing on top of the payload. Fail locally with a readable message
/// instead of building a tx the mempool silently drops.
const int kMaxDeployPayloadBytes = 120 * 1024;

/// Shared canonical top-level body. `payload` is the `{t, v}` discriminated
/// union; everything else is identical across the three kinds.
Map<String, dynamic> _canonicalBody({
  required String from,
  required int chainId,
  required int gasPrice,
  required int gasLimit,
  required int nonce,
  required Map<String, dynamic> payload,
}) {
  if (chainId <= 0) {
    throw ArgumentError('chainId must be positive, got $chainId');
  }
  if (nonce < 0) {
    throw ArgumentError('nonce must not be negative, got $nonce');
  }
  if (gasLimit <= 0 || gasPrice < 0) {
    throw ArgumentError('invalid gas: price=$gasPrice limit=$gasLimit');
  }
  return <String, dynamic>{
    'v': 1,
    'chainId': chainId,
    'from': decodeAddress(from).digest,
    'gas': <String, dynamic>{'price': gasPrice, 'limit': gasLimit},
    'payload': payload,
    'accessList': <dynamic>[],
    'nonce': nonce,
  };
}

/// Build a transfer body (kind=0). Amount is in nanos (1 ANM = 1e9 nanos).
///
/// `data` is an optional opaque payload written verbatim into the payload's
/// `data` bstr — used for on-chain memos like the App Store's `ANMSTORE1`
/// purchase memo. When omitted it is the empty bstr, which is exactly what
/// `normalize_tx_body` produces for a data-less transfer.
Map<String, dynamic> buildTransferBody({
  required String from,
  required String to,
  required BigInt amountNanos,
  required int nonce,
  Uint8List? data,
  int gasLimit = kDefaultTransferGasLimit,
  int maxFee = kDefaultGasPrice,
  int chainId = 1,
}) {
  if (data != null && data.length > kMaxTransferDataBytes) {
    throw ArgumentError(
      'transfer data too large: ${data.length} bytes > '
      '$kMaxTransferDataBytes-byte limit',
    );
  }
  if (amountNanos.isNegative) {
    throw ArgumentError('transfer amount must not be negative: $amountNanos');
  }
  return _canonicalBody(
    from: from,
    chainId: chainId,
    gasPrice: maxFee,
    gasLimit: gasLimit,
    nonce: nonce,
    payload: <String, dynamic>{
      't': 0,
      'v': <String, dynamic>{
        'to': decodeAddress(to).digest,
        'amount': amountNanos,
        'data': data ?? Uint8List(0),
      },
    },
  );
}

/// Build a deploy body (kind=1).
///
/// `value` exists for signature compatibility with the extension's
/// `AnimicaDeployArgs`, but the canonical deploy payload is `{code, manifest}`
/// — there is nowhere to put an amount. Rather than silently dropping funds
/// the caller asked to send, a non-zero value is rejected.
Map<String, dynamic> buildDeployBody({
  required String from,
  required Uint8List code,
  required Uint8List manifest,
  required int nonce,
  BigInt? value,
  int gasLimit = kDefaultDeployGasLimit,
  int maxFee = kDefaultGasPrice,
  int chainId = 1,
}) {
  if (code.isEmpty) {
    throw ArgumentError('deploy requires non-empty contract code bytes');
  }
  if (manifest.isEmpty) {
    throw ArgumentError('deploy requires non-empty manifest bytes');
  }
  if (code.length + manifest.length > kMaxDeployPayloadBytes) {
    throw ArgumentError(
      'deploy payload too large: ${code.length + manifest.length} bytes '
      '(code ${code.length} + manifest ${manifest.length}) > '
      '$kMaxDeployPayloadBytes-byte limit',
    );
  }
  if (value != null && value != BigInt.zero) {
    throw ArgumentError(
      'deploy transactions cannot carry a value: the canonical deploy payload '
      'is {code, manifest} with no amount field. Send the ANM in a separate '
      'transfer to the deployed contract.',
    );
  }
  return _canonicalBody(
    from: from,
    chainId: chainId,
    gasPrice: maxFee,
    gasLimit: gasLimit,
    nonce: nonce,
    payload: <String, dynamic>{
      't': 1,
      'v': <String, dynamic>{'code': code, 'manifest': manifest},
    },
  );
}

/// Build a contract-call body (kind=2). `calldata` is the pre-encoded
/// bytes returned by your encoder of choice (server-side encode-call,
/// or a known constant like the founders mint selector).
///
/// VALUE POLICY — a call CANNOT carry ANM. The canonical call payload is
/// `TxCall{to, data}` (core/types/tx.py); there is no amount slot, and the
/// only other way to express "value + data" on-chain is `t=0` transfer-with-
/// data, which moves the funds but never enters the contract. Both silent
/// outcomes are wrong in opposite directions (dropped funds vs. an unexecuted
/// call that still spends), so a non-zero `value` is REJECTED here and the
/// caller surfaces the error. `value: 0` / `null` is the only accepted form.
///
/// `calldata` must be non-empty: `TxCall.data` is validated non-empty by the
/// node, and an empty-data call is really a transfer — route it through
/// [buildTransferBody] instead.
Map<String, dynamic> buildCallBody({
  required String from,
  required String to,
  required Uint8List calldata,
  required int nonce,
  BigInt? value,
  int gasLimit = kDefaultCallGasLimit,
  int maxFee = kDefaultGasPrice,
  int chainId = 1,
}) {
  if (calldata.isEmpty) {
    throw ArgumentError(
      'contract calls require non-empty calldata (the node rejects an empty '
      'TxCall.data). Use buildTransferBody for a plain value transfer.',
    );
  }
  if (value != null && value != BigInt.zero) {
    throw ArgumentError(
      'contract calls cannot carry a value: the canonical call payload is '
      '{to, data} with no amount field. Sending it as a transfer instead '
      'would move the ANM without ever invoking the contract. Send the ANM '
      'in a separate transfer, or use a payable entry point that is funded '
      'up front.',
    );
  }
  return _canonicalBody(
    from: from,
    chainId: chainId,
    gasPrice: maxFee,
    gasLimit: gasLimit,
    nonce: nonce,
    payload: <String, dynamic>{
      't': 2,
      'v': <String, dynamic>{
        'to': decodeAddress(to).digest,
        'data': calldata,
      },
    },
  );
}

/// Chain identity that the canonical signing preimage binds a tx to.
///
/// The node does NOT verify a signature over the tx body alone. It rebuilds
/// `tx_signing_preimage` (python/animica/tx/signing.py):
///
///   preimage = canonicalCBOR({1: "animica.tx.v1", 2: chainId,
///                             3: genesisHash, 4: network, 5: "tx",
///                             6: bodyVersion, 7: normalize_tx_body(body)})
///
/// and verifies `sha3_512(build_sign_bytes(msg: preimage, …, forkId))` against
/// the signature (`tx_verify_signature`). Three of those inputs — the genesis
/// hash, the network name and the fork id — are chain metadata the wallet can
/// only learn from `chain.getChainIdentity`, which is why signing needs a
/// context object at all. Mirrors `fetchChainContext` in
/// apps/wallet-extension/src/tx/rpc.ts.
class AnimicaChainContext {
  final int chainId;
  final Uint8List genesisHash;
  final String network;
  final int? forkId;
  const AnimicaChainContext({
    required this.chainId,
    required this.genesisHash,
    required this.network,
    required this.forkId,
  });
}

/// Per-client cache of the chain identity.
///
/// The identity is chain-static (genesis hash, network name, fork id), so
/// re-fetching it on every send would add a pointless round trip to the
/// critical path of every payment. Keyed on the [RpcClient] instance so a
/// wallet pointed at a different node in the same session cannot reuse
/// another node's genesis hash. A failed fetch is NOT cached.
final Expando<Future<AnimicaChainContext>> _chainContextCache =
    Expando<Future<AnimicaChainContext>>('animicaChainContext');

/// Cached [fetchChainContext]. Use this on every signing path.
Future<AnimicaChainContext> chainContextFor(RpcClient rpc) {
  final cached = _chainContextCache[rpc];
  if (cached != null) return cached;
  final pending = _fetchAndCache(rpc);
  _chainContextCache[rpc] = pending;
  return pending;
}

/// Never cache a failure: a transient RPC outage must not permanently wedge
/// signing for the rest of the session.
Future<AnimicaChainContext> _fetchAndCache(RpcClient rpc) async {
  try {
    return await fetchChainContext(rpc);
  } catch (_) {
    _chainContextCache[rpc] = null;
    rethrow;
  }
}

/// Drop the cached identity for [rpc] (tests, or an endpoint switch).
void clearChainContextCache(RpcClient rpc) => _chainContextCache[rpc] = null;

/// Fetch the chain identity needed to sign. Tries the method aliases in the
/// same order the extension does, because older nodes only expose one of them.
///
/// The reported chain id is checked against the build's [AnimicaConfig.chainId]
/// and a mismatch is a hard error. This build's RPC endpoint list, its
/// `animica_chainId` answer and the id it signs into every preimage all have to
/// agree; signing a mainnet-shaped tx against a testnet identity (or vice
/// versa) produces a transaction that is either unverifiable or replayable on
/// the wrong chain.
Future<AnimicaChainContext> fetchChainContext(RpcClient rpc) async {
  Map<String, dynamic>? ident;
  Object? lastError;
  for (final method in const [
    'chain.getChainIdentity',
    'chain_getChainIdentity',
    'chain.getIdentity',
  ]) {
    try {
      final r = await rpc.call(method, {});
      if (r is Map) {
        ident = Map<String, dynamic>.from(r);
        break;
      }
    } catch (e) {
      lastError = e;
    }
  }
  if (ident == null) {
    throw RpcError(
      -32603,
      'Could not read the chain identity needed to sign '
      '(${lastError ?? 'empty result'}).',
    );
  }

  final chainId = _asInt(ident['chainId'] ?? ident['chain_id']);
  if (chainId == null || chainId <= 0) {
    throw RpcError(-32603, 'Chain identity has an invalid chainId: '
        '${ident['chainId'] ?? ident['chain_id']}');
  }
  if (chainId != AnimicaConfig.chainId) {
    throw RpcError(
      -32603,
      'Network mismatch: the node reports chain id $chainId but this wallet '
      'is built for chain ${AnimicaConfig.chainId}. Refusing to sign.',
    );
  }
  final genesisHex = ident['genesisHash'] ?? ident['genesis_hash'];
  if (genesisHex is! String || genesisHex.isEmpty) {
    throw RpcError(-32603, 'Chain identity is missing genesisHash.');
  }
  final genesis = _hexToBytes(genesisHex);
  if (genesis == null || genesis.length != 32) {
    throw RpcError(-32603,
        'Chain identity genesisHash must be 32 bytes, got "$genesisHex".');
  }
  // The node's own fallback chain is `network or name or "unknown"`
  // (rpc/methods/tx.py `_verify_pq_signature`). Mainnet's identity payload has
  // NO network key, so "unknown" is the value that actually gets signed — do
  // not "improve" this to a prettier default or every signature breaks.
  final network = (ident['network'] ?? ident['name'] ?? 'unknown').toString();

  return AnimicaChainContext(
    chainId: chainId,
    genesisHash: genesis,
    network: network,
    forkId: _asInt(ident['forkId'] ?? ident['fork_id']),
  );
}

/// Canonical tx signing preimage — the exact bytes the node reconstructs in
/// `tx_signing_preimage` and hashes into the signature.
///
/// `body` MUST already be in canonical shape (one of the three builders
/// above). The node puts `normalize_tx_body(body)` at key 7 and this function
/// puts `body` there; the two only agree because `normalize_tx_body` is an
/// identity passthrough for canonical bodies. Handing a flat
/// `{to,data,from,…}` map to this function silently produces the WRONG
/// preimage, so it is rejected outright.
Uint8List txSigningPreimage(
  Map<String, dynamic> body,
  AnimicaChainContext ctx,
) {
  assertCanonicalBody(body);
  return canonicalCbor(<int, dynamic>{
    1: 'animica.tx.v1',
    2: ctx.chainId,
    3: ctx.genesisHash,
    4: ctx.network,
    5: 'tx',
    6: body['v'] as int,
    7: body,
  });
}

/// Throw unless [body] is in the shape `normalize_tx_body` passes through
/// untouched: `v` + `gas` + `payload` all present, with a well-formed
/// `{t, v}` payload.
void assertCanonicalBody(Map<String, dynamic> body) {
  final missing = ['v', 'gas', 'payload'].where((k) => !body.containsKey(k));
  if (missing.isNotEmpty) {
    throw ArgumentError(
      'tx body is not canonical (missing ${missing.join(", ")}). The node '
      'rewrites any non-canonical body before hashing it, so signing this '
      'would produce a signature the chain rejects. Build it with '
      'buildTransferBody / buildCallBody / buildDeployBody.',
    );
  }
  if (body['v'] is! int) {
    throw ArgumentError('tx body `v` must be an int, got ${body['v']}');
  }
  final payload = body['payload'];
  if (payload is! Map || payload['t'] is! int || payload['v'] is! Map) {
    throw ArgumentError('tx body `payload` must be {t: int, v: map}');
  }
}

int? _asInt(Object? v) {
  if (v is int) return v;
  if (v is String) {
    final s = v.trim();
    if (s.isEmpty) return null;
    if (s.startsWith('0x') || s.startsWith('0X')) {
      return int.tryParse(s.substring(2), radix: 16);
    }
    return int.tryParse(s);
  }
  return null;
}

Uint8List? _hexToBytes(String hex) {
  var s = hex.trim();
  if (s.startsWith('0x') || s.startsWith('0X')) s = s.substring(2);
  if (s.isEmpty || s.length.isOdd) return null;
  final out = Uint8List(s.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    final b = int.tryParse(s.substring(i * 2, i * 2 + 2), radix: 16);
    if (b == null) return null;
    out[i] = b;
  }
  return out;
}

/// Sign a tx body with `account`. Returns the raw CBOR envelope ready
/// for `tx.sendRawTransaction`. Dispatches by `account.algId`:
///   0x1003 → ML-DSA-65 (real FIPS 204, chain v2 default — async)
///   0x1002 → SPHINCS-SHAKE-128s (deprecated commitment stub — refused)
///   0x1001 → Dilithium3 (deprecated commitment stub — refused)
///
/// Returns a Future because the ML-DSA-65 path runs through flutter_js
/// which is async-only.
///
/// `chainContext` is REQUIRED. It used to be optional, with the omitted case
/// falling back to signing the bare body CBOR — which is what every mobile
/// transfer, call, store purchase and dapp send did, and which the node
/// rejects 100% of the time (`tx_verify_signature` only ever reconstructs the
/// preimage). A tx that is silently unspendable is far worse than a loud
/// failure, so there is no longer a way to sign without one. Fetch it with
/// [chainContextFor].
Future<SignedTx> signTx({
  required Account account,
  required Map<String, dynamic> body,
  required AnimicaChainContext chainContext,
}) async {
  if (body['chainId'] != chainContext.chainId) {
    throw ArgumentError(
      'tx body chainId ${body['chainId']} does not match the signing context '
      '(${chainContext.chainId}).',
    );
  }
  final bodyCbor = canonicalCbor(body);
  final msg = txSigningPreimage(body, chainContext);
  final prehash = buildSignBytes(
    msg: msg,
    algId: account.algId,
    chainId: chainContext.chainId,
    forkId: chainContext.forkId,
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

/// Sign + broadcast in one shot. Returns the tx hash returned by the node.
Future<String> signAndBroadcast({
  required RpcClient rpc,
  required Account account,
  required Map<String, dynamic> body,
  required AnimicaChainContext chainContext,
}) async {
  final signed =
      await signTx(account: account, body: body, chainContext: chainContext);
  return rpc.sendRawTransaction(signed.rawTx);
}
