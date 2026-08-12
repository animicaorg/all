// ANM Instant (L2) client — Animica 10.0.0 ANM-native rollup.
//
// The node exposes its L2 over JSON-RPC on the SAME endpoint as L1, with
// every method prefixed `l2_`. This client sits on top of the existing
// [RpcClient] (services/rpc.dart) and speaks only those `l2_*` methods; it
// never touches the L1 tx path, so ANM (L1) and ANM Instant (L2) stay two
// DISTINCT balances of the same asset.
//
// The signing recipe is identical in every Animica wallet:
//
//   1. l2_prepareTransfer(intent) -> {signingHash, bodyHex, fee, nonce, …}.
//      The node builds the L2 binary body and returns the 64-byte
//      sha3-512 `signingHash` to sign. We NEVER re-implement that codec —
//      the node is the single source of truth for the bytes.
//   2. Sign the 64-byte `signingHash` DIRECTLY with the account's EXISTING
//      ML-DSA-65 signer (alg 0x1003 — the same key used for L1). Do NOT
//      re-hash and do NOT reuse signer.dart's L1 `tx_signing_preimage`
//      builder: the node already produced the exact message to sign.
//   3. l2_submitSigned({body, pubkey, signature}) -> "0x"+txid.
//   4. Poll l2_getTransaction until PROVEN. SOFT_CONFIRMED is sequencer
//      acceptance only and must never be shown as L1 settlement.
//
// The L2 address == the L1 address (both are
// sha3_256(u16be(0x1003) || pubkey)), so the active account's pubkey /
// address / secret key are reused unchanged — a user's existing account
// works on L2 with no migration.

import 'dart:async';
import 'dart:typed_data';

import '../models/account.dart';
import 'ml_dsa_65.dart';
import 'rpc.dart';

/// Terminal + intermediate states of an L2 transaction, in lifecycle order.
/// Mirrors the node's `l2_getTransaction.status` string enum.
enum L2TxStatus {
  received,
  validated,
  softConfirmed,
  batched,
  proven,
  l1Submitted,
  l1Finalized,
  failed,
  reverted,
  unknown;

  static L2TxStatus parse(String? s) {
    switch ((s ?? '').toUpperCase()) {
      case 'RECEIVED':
        return L2TxStatus.received;
      case 'VALIDATED':
        return L2TxStatus.validated;
      case 'SOFT_CONFIRMED':
        return L2TxStatus.softConfirmed;
      case 'BATCHED':
        return L2TxStatus.batched;
      case 'PROVEN':
        return L2TxStatus.proven;
      case 'L1_SUBMITTED':
        return L2TxStatus.l1Submitted;
      case 'L1_FINALIZED':
        return L2TxStatus.l1Finalized;
      case 'FAILED':
        return L2TxStatus.failed;
      case 'REVERTED':
        return L2TxStatus.reverted;
      default:
        return L2TxStatus.unknown;
    }
  }

  /// The wire string the node uses (round-trips [parse]).
  String get wire {
    switch (this) {
      case L2TxStatus.received:
        return 'RECEIVED';
      case L2TxStatus.validated:
        return 'VALIDATED';
      case L2TxStatus.softConfirmed:
        return 'SOFT_CONFIRMED';
      case L2TxStatus.batched:
        return 'BATCHED';
      case L2TxStatus.proven:
        return 'PROVEN';
      case L2TxStatus.l1Submitted:
        return 'L1_SUBMITTED';
      case L2TxStatus.l1Finalized:
        return 'L1_FINALIZED';
      case L2TxStatus.failed:
        return 'FAILED';
      case L2TxStatus.reverted:
        return 'REVERTED';
      case L2TxStatus.unknown:
        return 'UNKNOWN';
    }
  }

  /// PROVEN or later — the L2 batch carries a validity proof, i.e. the
  /// transfer is settled on L2. This is the point a send is "done" for the
  /// user; L1_FINALIZED additionally means the batch is settled on L1.
  bool get isProven =>
      this == L2TxStatus.proven ||
      this == L2TxStatus.l1Submitted ||
      this == L2TxStatus.l1Finalized;

  /// A state from which the tx will not advance further — success (proven /
  /// finalized) OR failure (failed / reverted). Polling stops here.
  bool get isTerminal =>
      isProven || this == L2TxStatus.failed || this == L2TxStatus.reverted;

  bool get isFailure =>
      this == L2TxStatus.failed || this == L2TxStatus.reverted;
}

/// The kind of L2 transfer, matching the `kind` param of l2_prepareTransfer.
enum L2Kind {
  transfer,
  pay,
  withdraw;

  String get wire {
    switch (this) {
      case L2Kind.transfer:
        return 'transfer';
      case L2Kind.pay:
        return 'pay';
      case L2Kind.withdraw:
        return 'withdraw';
    }
  }
}

/// Result of l2_getBalance — the account's ANM Instant balance, distinct
/// from its L1 balance.
class L2Balance {
  final String address;
  final BigInt balance; // nanos
  final int nonce;
  final int pendingNonce;
  final String unit;

  const L2Balance({
    required this.address,
    required this.balance,
    required this.nonce,
    required this.pendingNonce,
    required this.unit,
  });

  factory L2Balance.fromJson(Map<String, dynamic> j) => L2Balance(
        address: (j['address'] ?? '').toString(),
        balance: _parseBig(j['balance']),
        nonce: _asInt(j['nonce']),
        pendingNonce: _asInt(j['pendingNonce'] ?? j['nonce']),
        unit: (j['unit'] ?? 'nanos').toString(),
      );
}

/// The node's echoed, ready-to-sign transfer. The `signingHash` is the exact
/// 64-byte message to sign; the other fields are echoed for the wallet to
/// display and verify BEFORE signing.
class L2Prepared {
  final L2Kind kind;
  final String sender;
  final String recipient;
  final BigInt amount; // nanos
  final int nonce;
  final BigInt fee; // nanos
  final BigInt requiredFee; // nanos
  final int l2ChainId;
  final Uint8List body; // decoded from bodyHex
  final String bodyHex; // 0x…
  final Uint8List signingHash; // 64 bytes (sha3-512)
  final String sigScheme;

  const L2Prepared({
    required this.kind,
    required this.sender,
    required this.recipient,
    required this.amount,
    required this.nonce,
    required this.fee,
    required this.requiredFee,
    required this.l2ChainId,
    required this.body,
    required this.bodyHex,
    required this.signingHash,
    required this.sigScheme,
  });

  factory L2Prepared.fromJson(Map<String, dynamic> j) {
    final bodyHex = (j['bodyHex'] ?? '').toString();
    final signingHex = (j['signingHash'] ?? '').toString();
    final body = _hexToBytes(bodyHex);
    final signingHash = _hexToBytes(signingHex);
    if (signingHash.length != 64) {
      throw StateError(
          'l2_prepareTransfer returned a ${signingHash.length}-byte signingHash '
          '(expected 64). Refusing to sign an unexpected message.');
    }
    return L2Prepared(
      kind: _parseKind(j['kind']),
      sender: (j['sender'] ?? '').toString(),
      recipient: (j['recipient'] ?? '').toString(),
      amount: _parseBig(j['amount']),
      nonce: _asInt(j['nonce']),
      fee: _parseBig(j['fee']),
      requiredFee: _parseBig(j['requiredFee'] ?? j['fee']),
      l2ChainId: _asInt(j['l2ChainId']),
      body: body,
      bodyHex: bodyHex.startsWith('0x') ? bodyHex : '0x$bodyHex',
      signingHash: signingHash,
      sigScheme: (j['sigScheme'] ?? 'ml_dsa_65').toString(),
    );
  }
}

/// Terminal-ish view of an L2 tx returned by l2_getTransaction.
class L2Transaction {
  final String txid;
  final L2TxStatus status;
  final int? batch;
  final String? reason;
  final Map<String, dynamic> raw;

  const L2Transaction({
    required this.txid,
    required this.status,
    required this.batch,
    required this.reason,
    required this.raw,
  });

  factory L2Transaction.fromJson(Map<String, dynamic> j) => L2Transaction(
        txid: (j['txid'] ?? '').toString(),
        status: L2TxStatus.parse(j['status']?.toString()),
        batch: j['batch'] is int ? j['batch'] as int : null,
        reason: j['reason']?.toString(),
        raw: j,
      );
}

/// Outcome of a full send/withdraw recipe run.
class L2SendResult {
  final String txid;
  final L2TxStatus status;
  final L2Prepared prepared;
  final String? reason;

  const L2SendResult({
    required this.txid,
    required this.status,
    required this.prepared,
    this.reason,
  });
}

/// Signs the 64-byte L2 `signingHash` for [account]. Injected so tests can
/// avoid the flutter_js JS engine; the default binds to the real ML-DSA-65
/// signer, the SAME primitive L1 sends use.
typedef L2SignFn = Future<Uint8List> Function(
    Account account, Uint8List signingHash);

class L2Client {
  final RpcClient rpc;
  final L2SignFn _sign;

  L2Client(this.rpc, {L2SignFn? signer}) : _sign = signer ?? _defaultSigner;

  /// Default signer: sign the L2 signingHash DIRECTLY with the account's
  /// existing ML-DSA-65 secret key. The node already produced the message —
  /// no L1 preimage builder, no extra hashing.
  static Future<Uint8List> _defaultSigner(
      Account account, Uint8List signingHash) {
    return MlDsa65.sign(account.secretKey, signingHash);
  }

  // ── thin JSON-RPC wrappers (all methods live on the L1 endpoint) ──────

  Future<int> l2ChainId() async {
    final r = await rpc.call('l2_chainId', const {});
    return _asInt(r);
  }

  Future<Map<String, dynamic>> l2Status() async {
    final r = await rpc.call('l2_status', const {});
    return r is Map ? Map<String, dynamic>.from(r) : <String, dynamic>{};
  }

  Future<L2Balance> l2GetBalance(String address) async {
    final r = await rpc.call('l2_getBalance', {'address': address});
    if (r is! Map) {
      throw RpcError(-32603, 'unexpected l2_getBalance result shape');
    }
    return L2Balance.fromJson(Map<String, dynamic>.from(r));
  }

  Future<L2Transaction?> l2GetTransaction(String txid) async {
    try {
      final r = await rpc.call('l2_getTransaction', {'txid': txid});
      if (r is Map) {
        return L2Transaction.fromJson(Map<String, dynamic>.from(r));
      }
    } on RpcError {
      // Not yet indexed / unknown txid — treat as "no info yet".
    }
    return null;
  }

  /// Current L2 throughput (transactions per second), best-effort.
  Future<double> l2GetTPS() async {
    try {
      final r = await rpc.call('l2_getTPS', const {});
      if (r is num) return r.toDouble();
      if (r is Map) {
        final v = r['tps'] ?? r['value'];
        if (v is num) return v.toDouble();
        if (v is String) return double.tryParse(v) ?? 0.0;
      }
      if (r is String) return double.tryParse(r) ?? 0.0;
    } on RpcError {
      // Older node without the metric — report 0.
    }
    return 0.0;
  }

  /// Raw l2_prepareTransfer. Prefer [prepareTransfer] for a typed result.
  Future<Map<String, dynamic>> l2PrepareTransfer(
      Map<String, dynamic> params) async {
    final r = await rpc.call('l2_prepareTransfer', params);
    if (r is! Map) {
      throw RpcError(-32603, 'unexpected l2_prepareTransfer result shape');
    }
    return Map<String, dynamic>.from(r);
  }

  /// Raw l2_submitSigned -> "0x"+txid.
  Future<String> l2SubmitSigned({
    required String body,
    required String pubkey,
    required String signature,
  }) async {
    final r = await rpc.call('l2_submitSigned', {
      'body': body,
      'pubkey': pubkey,
      'signature': signature,
    });
    if (r is String) return r;
    if (r is Map && r['txid'] is String) return r['txid'] as String;
    throw RpcError(-32603, 'unexpected l2_submitSigned result shape');
  }

  // ── recipe steps ──────────────────────────────────────────────────────

  /// Step 1 — ask the node to build the transfer and hand back the exact
  /// bytes to sign. `amountNanos` is integer nanos (1 ANM = 1e9). Address
  /// may be `anim1…` or 0x-hex 32-byte; the node accepts both.
  Future<L2Prepared> prepareTransfer({
    required L2Kind kind,
    required String sender,
    required String recipient,
    required BigInt amountNanos,
    String? memo,
    int? nonce,
    BigInt? fee,
    int? expiry,
  }) async {
    final params = <String, dynamic>{
      'kind': kind.wire,
      'sender': sender,
      'recipient': recipient,
      'amount': _bigToJson(amountNanos),
      if (memo != null && memo.isNotEmpty) 'memo': memo,
      if (nonce != null) 'nonce': nonce,
      if (fee != null) 'fee': _bigToJson(fee),
      if (expiry != null) 'expiry': expiry,
    };
    final raw = await l2PrepareTransfer(params);
    return L2Prepared.fromJson(raw);
  }

  /// Steps 2+3 — sign the prepared `signingHash` with the account's existing
  /// ML-DSA-65 key and submit. Returns the txid ("0x…").
  Future<String> signAndSubmit(Account account, L2Prepared prepared) async {
    if (account.algId != 0x1003) {
      // The L2 sig scheme is ML-DSA-65, the same key L1 uses. Legacy stub
      // schemes are forgeable and rejected — refuse rather than build an
      // unspendable L2 tx.
      throw UnsupportedError(
        'ANM Instant requires an ML-DSA-65 account (alg 0x1003); this account '
        'is 0x${account.algId.toRadixString(16)}.',
      );
    }
    final sig = await _sign(account, prepared.signingHash);
    return l2SubmitSigned(
      body: prepared.bodyHex,
      pubkey: '0x${_bytesToHex(account.publicKey)}',
      signature: '0x${_bytesToHex(sig)}',
    );
  }

  /// Step 4 — poll l2_getTransaction until the tx reaches a terminal state
  /// (PROVEN / L1_FINALIZED, or FAILED / REVERTED). `onStatus` fires on every
  /// observed status so the UI can advance its chip live (including through
  /// SOFT_CONFIRMED, which is NOT settlement). Returns the last status seen.
  Future<L2Transaction?> pollTransaction(
    String txid, {
    void Function(L2Transaction tx)? onStatus,
    Duration interval = const Duration(seconds: 2),
    int maxPolls = 60,
  }) async {
    L2Transaction? last;
    for (var i = 0; i < maxPolls; i++) {
      final tx = await l2GetTransaction(txid);
      if (tx != null) {
        last = tx;
        onStatus?.call(tx);
        if (tx.status.isTerminal) return tx;
      }
      await Future<void>.delayed(interval);
    }
    return last;
  }

  // ── high-level recipes ────────────────────────────────────────────────

  /// Full Send-Instant recipe: prepare → sign → submit → poll to PROVEN.
  ///
  /// `to` is the recipient (anim1… or 0x-hex). If [confirm] is provided it is
  /// awaited with the node-echoed [L2Prepared] AFTER prepare and BEFORE
  /// signing, so the UI can show the exact recipient/amount/fee the node will
  /// charge and let the user cancel (return false) — nothing is signed until
  /// then. Returns null if the user declined.
  Future<L2SendResult?> sendInstant({
    required Account account,
    required String to,
    required BigInt amountNanos,
    String? memo,
    Future<bool> Function(L2Prepared prepared)? confirm,
    void Function(L2TxStatus status)? onStatus,
    bool waitForProven = true,
    Duration pollInterval = const Duration(seconds: 2),
    int maxPolls = 60,
  }) {
    return _runRecipe(
      account: account,
      kind: L2Kind.transfer,
      recipient: to,
      amountNanos: amountNanos,
      memo: memo,
      confirm: confirm,
      onStatus: onStatus,
      waitForProven: waitForProven,
      pollInterval: pollInterval,
      maxPolls: maxPolls,
    );
  }

  /// Withdraw L2 → L1 for the same account. The recipient defaults to the
  /// account's own L1 address (== its L2 address); a different L1 payout
  /// target may be supplied. After PROVEN, call [l2GetWithdrawalProof] for
  /// the L1 claim data.
  Future<L2SendResult?> withdrawToL1({
    required Account account,
    required BigInt amountNanos,
    String? toL1Address,
    Future<bool> Function(L2Prepared prepared)? confirm,
    void Function(L2TxStatus status)? onStatus,
    bool waitForProven = true,
    Duration pollInterval = const Duration(seconds: 2),
    int maxPolls = 60,
  }) {
    return _runRecipe(
      account: account,
      kind: L2Kind.withdraw,
      recipient: toL1Address ?? account.address,
      amountNanos: amountNanos,
      confirm: confirm,
      onStatus: onStatus,
      waitForProven: waitForProven,
      pollInterval: pollInterval,
      maxPolls: maxPolls,
    );
  }

  Future<L2SendResult?> _runRecipe({
    required Account account,
    required L2Kind kind,
    required String recipient,
    required BigInt amountNanos,
    String? memo,
    Future<bool> Function(L2Prepared prepared)? confirm,
    void Function(L2TxStatus status)? onStatus,
    required bool waitForProven,
    required Duration pollInterval,
    required int maxPolls,
  }) async {
    final prepared = await prepareTransfer(
      kind: kind,
      sender: account.address,
      recipient: recipient,
      amountNanos: amountNanos,
      memo: memo,
    );
    if (confirm != null) {
      final ok = await confirm(prepared);
      if (!ok) return null;
    }
    final txid = await signAndSubmit(account, prepared);
    if (!waitForProven) {
      return L2SendResult(
          txid: txid, status: L2TxStatus.received, prepared: prepared);
    }
    final tx = await pollTransaction(
      txid,
      onStatus: (t) => onStatus?.call(t.status),
      interval: pollInterval,
      maxPolls: maxPolls,
    );
    return L2SendResult(
      txid: txid,
      status: tx?.status ?? L2TxStatus.unknown,
      prepared: prepared,
      reason: tx?.reason,
    );
  }

  /// L1 claim data for a completed withdrawal, keyed by its nullifier.
  Future<Map<String, dynamic>?> l2GetWithdrawalProof(String nullifier) async {
    try {
      final r = await rpc.call('l2_getWithdrawalProof', {'nullifier': nullifier});
      return r is Map ? Map<String, dynamic>.from(r) : null;
    } on RpcError {
      return null;
    }
  }
}

// ── shared helpers ────────────────────────────────────────────────────────

L2Kind _parseKind(Object? v) {
  switch ((v ?? '').toString().toLowerCase()) {
    case 'pay':
      return L2Kind.pay;
    case 'withdraw':
      return L2Kind.withdraw;
    default:
      return L2Kind.transfer;
  }
}

/// JSON-encodable form of an amount in nanos. Amounts within 2^53 stay ints
/// (what the node expects); anything larger goes as a decimal string so
/// jsonEncode never throws on a BigInt and no precision is lost.
Object _bigToJson(BigInt v) {
  if (v.isValidInt && v.abs() < BigInt.from(1) << 53) return v.toInt();
  return v.toString();
}

BigInt _parseBig(Object? v) {
  if (v == null) return BigInt.zero;
  if (v is int) return BigInt.from(v);
  if (v is BigInt) return v;
  if (v is double) return BigInt.from(v);
  if (v is String) {
    final s = v.trim();
    if (s.isEmpty) return BigInt.zero;
    if (s.startsWith('0x') || s.startsWith('0X')) {
      return BigInt.parse(s.substring(2), radix: 16);
    }
    return BigInt.tryParse(s) ?? BigInt.zero;
  }
  return BigInt.zero;
}

int _asInt(Object? v) {
  if (v is int) return v;
  if (v is BigInt) return v.toInt();
  if (v is double) return v.toInt();
  if (v is String) {
    final s = v.trim();
    if (s.startsWith('0x') || s.startsWith('0X')) {
      return int.tryParse(s.substring(2), radix: 16) ?? 0;
    }
    return int.tryParse(s) ?? 0;
  }
  return 0;
}

Uint8List _hexToBytes(String hex) {
  var s = hex.trim();
  if (s.startsWith('0x') || s.startsWith('0X')) s = s.substring(2);
  if (s.isEmpty) return Uint8List(0);
  if (s.length.isOdd) {
    throw FormatException('odd-length hex string: "$hex"');
  }
  final out = Uint8List(s.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    final b = int.tryParse(s.substring(i * 2, i * 2 + 2), radix: 16);
    if (b == null) throw FormatException('invalid hex byte in "$hex"');
    out[i] = b;
  }
  return out;
}

String _bytesToHex(Uint8List b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
