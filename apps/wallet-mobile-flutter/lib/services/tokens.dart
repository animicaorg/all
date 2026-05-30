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
import 'package:pointycastle/digests/sha3.dart';

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
// Animica contract calls reach `state.call` as raw bytes. The contract's
// own ABI dictates encoding; for the canonical ANM-20 set (`balance_of`,
// `name`, `symbol`, `decimals`) we use the same shape the launcher-api's
// encode_calldata.py produces: 4-byte selector = sha3_256(method)[:4],
// then args concatenated by their declared serialization (uvarint
// length prefix for bytes, big-endian for ints).
//
// If a particular token contract uses a different encoder, the user can
// override its values via TokenSpec.

Uint8List _selector(String method) {
  final d = SHA3Digest(256);
  final input = Uint8List.fromList(utf8.encode(method));
  d.update(input, 0, input.length);
  final full = Uint8List(32);
  d.doFinal(full, 0);
  return full.sublist(0, 4);
}

Uint8List _encodeNoArgs(String method) => _selector(method);

Uint8List _encodeWithBytesArg(String method, Uint8List arg) {
  final sel = _selector(method);
  final out = Uint8List(sel.length + 2 + arg.length);
  out.setRange(0, sel.length, sel);
  // 2-byte big-endian length prefix.
  out[sel.length] = (arg.length >> 8) & 0xff;
  out[sel.length + 1] = arg.length & 0xff;
  out.setRange(sel.length + 2, out.length, arg);
  return out;
}

Future<TokenBalance?> fetchTokenBalance({
  required RpcClient rpc,
  required String holder,
  required TokenSpec spec,
}) async {
  // balance_of(holder) — argument is the 32-byte address digest.
  final addrBytes = decodeAddress(holder).digest;
  final balRaw = await rpc.stateCall(
    contract: spec.contract,
    data: _encodeWithBytesArg('balance_of', addrBytes),
  );
  if (balRaw == null) return null;
  final balance = _decodeUint(balRaw);

  String symbol = spec.overrideSymbol ?? 'ANM-20';
  String? name = spec.overrideName;
  int decimals = spec.overrideDecimals ?? 9;

  if (spec.overrideSymbol == null) {
    final s = await rpc.stateCall(
      contract: spec.contract,
      data: _encodeNoArgs('symbol'),
    );
    if (s != null) {
      final asString = _decodeBytesAsString(s);
      if (asString != null && asString.isNotEmpty) symbol = asString;
    }
  }
  if (spec.overrideName == null) {
    final n = await rpc.stateCall(
      contract: spec.contract,
      data: _encodeNoArgs('name'),
    );
    if (n != null) name = _decodeBytesAsString(n);
  }
  if (spec.overrideDecimals == null) {
    final d = await rpc.stateCall(
      contract: spec.contract,
      data: _encodeNoArgs('decimals'),
    );
    if (d != null) {
      final dec = _decodeUint(d).toInt();
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

BigInt _decodeUint(Uint8List b) {
  if (b.isEmpty) return BigInt.zero;
  // Two common encodings: raw big-endian, or CBOR uint head. Heuristic:
  // if the leading byte's major-type bits indicate CBOR uint (0), use the
  // CBOR decode path; otherwise fall through to plain big-endian.
  final major = (b[0] >> 5) & 0x07;
  if (major == 0) {
    final v = b[0] & 0x1f;
    if (v < 24) return BigInt.from(v);
    if (v == 24 && b.length >= 2) return BigInt.from(b[1]);
    if (v == 25 && b.length >= 3) return BigInt.from((b[1] << 8) | b[2]);
    if (v == 26 && b.length >= 5) {
      return BigInt.from((b[1] << 24) | (b[2] << 16) | (b[3] << 8) | b[4]);
    }
    if (v == 27 && b.length >= 9) {
      var x = BigInt.zero;
      for (var i = 1; i <= 8; i++) {
        x = (x << 8) | BigInt.from(b[i]);
      }
      return x;
    }
  }
  var x = BigInt.zero;
  for (final byte in b) {
    x = (x << 8) | BigInt.from(byte);
  }
  return x;
}

String? _decodeBytesAsString(Uint8List b) {
  if (b.isEmpty) return null;
  try {
    final major = (b[0] >> 5) & 0x07;
    if (major == 2 || major == 3) {
      final v = b[0] & 0x1f;
      var off = 1;
      int len;
      if (v < 24) {
        len = v;
      } else if (v == 24) {
        len = b[1];
        off = 2;
      } else if (v == 25) {
        len = (b[1] << 8) | b[2];
        off = 3;
      } else {
        return utf8.decode(b, allowMalformed: true);
      }
      if (off + len <= b.length) {
        return utf8.decode(b.sublist(off, off + len), allowMalformed: true);
      }
    }
  } catch (_) {}
  try {
    return utf8.decode(b, allowMalformed: true);
  } catch (_) {
    return null;
  }
}
