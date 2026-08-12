// ANM-20 token tracking.
//
// Token discovery without a chain-side indexer is hard — there's no
// per-account "tokens I own" view. We use a watch-list: the user
// manually adds a token's bech32m contract address (or hex), and the
// wallet queries each one's `balance_of(holder)`, `name()`, `symbol()`
// and `decimals()` views via `state.call`.
//
// The watch-list is persisted alongside the password vault under
// `animica.wallet.tokens.v1` in plaintext (it doesn't contain secrets).
// A future v0.3 can layer in an indexer-backed auto-discovery.

import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'abi_codec.dart';
import 'address.dart';
import 'rpc.dart';

const _kTokensKey = 'animica.wallet.tokens.v1';

class TokenSpec {
  /// `anim1…` contract address.
  final String contract;
  final String? overrideSymbol;
  final String? overrideName;
  final int? overrideDecimals;
  const TokenSpec({
    required this.contract,
    this.overrideSymbol,
    this.overrideName,
    this.overrideDecimals,
  });

  Map<String, dynamic> toJson() => {
        'contract': contract,
        if (overrideSymbol != null) 'symbol': overrideSymbol,
        if (overrideName != null) 'name': overrideName,
        if (overrideDecimals != null) 'decimals': overrideDecimals,
      };

  static TokenSpec fromJson(Map<String, dynamic> j) => TokenSpec(
        contract: j['contract'] as String,
        overrideSymbol: j['symbol'] as String?,
        overrideName: j['name'] as String?,
        overrideDecimals: j['decimals'] as int?,
      );
}

class TokenBalance {
  final TokenSpec spec;
  final BigInt balance;     // raw, in token base units
  final int decimals;
  final String symbol;
  final String? name;
  const TokenBalance({
    required this.spec,
    required this.balance,
    required this.decimals,
    required this.symbol,
    this.name,
  });

  String formatted({int maxDecimals = 4}) {
    if (decimals == 0) return balance.toString();
    final divisor = BigInt.from(10).pow(decimals);
    final whole = balance ~/ divisor;
    final frac = balance % divisor;
    if (frac == BigInt.zero) return whole.toString();
    var fracStr = frac.toString().padLeft(decimals, '0');
    final keep = maxDecimals < fracStr.length ? maxDecimals : fracStr.length;
    fracStr = fracStr.substring(0, keep).replaceAll(RegExp(r'0+$'), '');
    return fracStr.isEmpty ? whole.toString() : '$whole.$fracStr';
  }
}

class TokenWatchlist {
  final FlutterSecureStorage _storage;
  TokenWatchlist({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  Future<List<TokenSpec>> load() async {
    final raw = await _storage.read(key: _kTokensKey);
    if (raw == null || raw.isEmpty) return const [];
    final j = jsonDecode(raw);
    if (j is! List) return const [];
    return j
        .whereType<Map<String, dynamic>>()
        .map(TokenSpec.fromJson)
        .toList(growable: false);
  }

  Future<void> save(List<TokenSpec> tokens) =>
      _storage.write(
          key: _kTokensKey,
          value: jsonEncode(tokens.map((t) => t.toJson()).toList()));

  Future<void> add(TokenSpec t) async {
    final cur = await load();
    final filtered = cur.where((x) => x.contract != t.contract).toList()..add(t);
    await save(filtered);
  }

  Future<void> remove(String contract) async {
    final cur = await load();
    await save(cur.where((x) => x.contract != contract).toList());
  }
}

// ── ABI helpers ────────────────────────────────────────────────────────
//
// ANM-20 view calls reach `state.call` as animica:abi:v1 calldata and their
// return payloads come back in the same encoding — see services/abi_codec.dart,
// which is byte-verified against the chain's own omni_sdk codec. The previous
// 4-byte-selector scheme here never matched the standard contract's 8-byte
// selector, so these calls silently mismatched; this uses the real codec.
//
// If a particular token contract uses a different encoder, the user can still
// override name/symbol/decimals via TokenSpec.

/// The canonical ANM-20 view ABI (the subset the watch-list reads).
final List<AbiFn> _tokenViewAbi = parseAbiFunctions({
  'functions': [
    {
      'name': 'balance_of',
      'inputs': [{'name': 'addr', 'type': 'bytes'}],
      'outputs': [{'type': 'int'}],
    },
    {'name': 'symbol', 'inputs': [], 'outputs': [{'type': 'bytes'}]},
    {'name': 'name', 'inputs': [], 'outputs': [{'type': 'bytes'}]},
    {'name': 'decimals', 'inputs': [], 'outputs': [{'type': 'int'}]},
  ],
});

Future<TokenBalance?> fetchTokenBalance({
  required RpcClient rpc,
  required String holder,
  required TokenSpec spec,
}) async {
  // balance_of(holder) — argument is the 32-byte address digest.
  final addrBytes = decodeAddress(holder).digest;
  final balRaw = await rpc.stateCall(
    contract: spec.contract,
    data: encodeCall(_tokenViewAbi, 'balance_of', [addrBytes]),
  );
  if (balRaw == null) return null;
  final balance = _asBigInt(decodeReturn(_tokenViewAbi, 'balance_of', balRaw));

  String symbol = spec.overrideSymbol ?? 'ANM-20';
  String? name = spec.overrideName;
  int decimals = spec.overrideDecimals ?? 9;

  if (spec.overrideSymbol == null) {
    final s = await rpc.stateCall(
      contract: spec.contract,
      data: encodeCall(_tokenViewAbi, 'symbol', const []),
    );
    if (s != null) {
      final asString = _bytesReturnAsString(decodeReturn(_tokenViewAbi, 'symbol', s));
      if (asString != null && asString.isNotEmpty) symbol = asString;
    }
  }
  if (spec.overrideName == null) {
    final n = await rpc.stateCall(
      contract: spec.contract,
      data: encodeCall(_tokenViewAbi, 'name', const []),
    );
    if (n != null) name = _bytesReturnAsString(decodeReturn(_tokenViewAbi, 'name', n));
  }
  if (spec.overrideDecimals == null) {
    final d = await rpc.stateCall(
      contract: spec.contract,
      data: encodeCall(_tokenViewAbi, 'decimals', const []),
    );
    if (d != null) {
      final dec = _asBigInt(decodeReturn(_tokenViewAbi, 'decimals', d)).toInt();
      if (dec >= 0 && dec <= 36) decimals = dec;
    }
  }

  return TokenBalance(
    spec: spec,
    balance: balance,
    decimals: decimals,
    symbol: symbol,
    name: name,
  );
}

BigInt _asBigInt(Object? v) {
  if (v is BigInt) return v;
  if (v is int) return BigInt.from(v);
  return BigInt.zero;
}

String? _bytesReturnAsString(Object? v) {
  if (v is Uint8List) {
    if (v.isEmpty) return null;
    try {
      return utf8.decode(v);
    } catch (_) {
      return utf8.decode(v, allowMalformed: true);
    }
  }
  if (v is String) return v;
  return null;
}
