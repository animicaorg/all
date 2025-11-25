/*
 * Animica Wallet — Randomness (Commit/Reveal & Beacon) Client
 *
 * RPC surface (tolerant to variants):
 *   - animica_random_commit(commitHex[, {epoch:int}]) → "0xTX" | {ok:true, txHash:"0x…"} | true
 *   - animica_random_reveal(preimageHex[, {epoch:int}]) → same as above
 *   - animica_random_beacon([ {epoch:int} ]) → "0x…" | {epoch:int, value:"0x…"} | {beacon:"0x…"}
 *   - animica_random_epoch() → int (optional helper on some nodes)
 *
 * Helpers provided:
 *   • makePreimage(seed[, saltLen]) → Uint8List
 *   • commitmentOf(preimage) → Uint8List (sha256)
 *   • verifyReveal(preimage, commitment) → bool
 *
 * High-level API:
 *   • commitCommitment(commitment, {epoch}) → String (tx hash or "ok")
 *   • commitFromPreimage(preimage, {epoch}) → String (tx hash or "ok")
 *   • reveal(preimage, {epoch}) → String (tx hash or "ok")
 *   • getBeacon({epoch}) → RandomBeacon
 *   • currentEpoch() → int (best-effort; falls back to beacon.epoch or 0)
 */

import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' show sha256;

import 'rpc_client.dart';
import 'env.dart';

class RandomBeacon {
  final int epoch;
  final Uint8List value;        // raw bytes of beacon
  RandomBeacon({required this.epoch, required this.value});

  String get hex => _hex0x(value);

  @override
  String toString() => 'RandomBeacon(epoch:$epoch, hex:$hex)';
}

class RandomnessClient {
  final RpcClient rpc;
  RandomnessClient(this.rpc);
  factory RandomnessClient.fromEnv() => RandomnessClient(RpcClient.fromEnv());

  // ---------- High-level commit/reveal ----------

  /// Commit a preimage by *sending the commitment bytes* (sha256(preimage)).
  Future<String> commitFromPreimage(Uint8List preimage, {int? epoch}) async {
    final c = commitmentOf(preimage);
    return commitCommitment(c, epoch: epoch);
  }

  /// Commit already-computed [commitment] (raw bytes).
  Future<String> commitCommitment(Uint8List commitment, {int? epoch}) async {
    final params = <Object?>[_hex0x(commitment)];
    if (epoch != null) params.add({'epoch': epoch});
    final res = await rpc.call<dynamic>('animica_random_commit', params);
    return _asTxAck(res);
  }

  /// Reveal the [preimage] (raw bytes) to finalize the commit/reveal scheme.
  Future<String> reveal(Uint8List preimage, {int? epoch}) async {
    final params = <Object?>[_hex0x(preimage)];
    if (epoch != null) params.add({'epoch': epoch});
    final res = await rpc.call<dynamic>('animica_random_reveal', params);
    return _asTxAck(res);
  }

  // ---------- Beacon queries ----------

  /// Get the randomness beacon for a specific [epoch] or the latest if null.
  Future<RandomBeacon> getBeacon({int? epoch}) async {
    final params = epoch == null ? const [] : [ {'epoch': epoch} ];
    final res = await rpc.call<dynamic>('animica_random_beacon', params);

    int outEpoch = epoch ?? 0;
    Uint8List? val;

    if (res is String) {
      // Node returned just the hex string; epoch might be unknown
      val = _bytesFromAnyString(res);
    } else if (res is Map) {
      final m = res.map((k, v) => MapEntry(k.toString(), v));
      final v = m['value'] ?? m['beacon'] ?? m['data'] ?? m['hex'];
      if (m['epoch'] != null) {
        outEpoch = _asInt(m['epoch']);
      }
      if (v is String) {
        val = _bytesFromAnyString(v);
      } else if (v is List<int>) {
        val = Uint8List.fromList(v);
      }
    } else if (res is List && res.isNotEmpty) {
      // Some implementations return [epoch, "0x…"]
      final e = res[0];
      final v = res.length > 1 ? res[1] : null;
      outEpoch = _asInt(e);
      if (v is String) val = _bytesFromAnyString(v);
    }

    val ??= Uint8List(0);
    return RandomBeacon(epoch: outEpoch, value: val);
  }

  /// Best-effort current epoch.
  Future<int> currentEpoch() async {
    try {
      final e = await rpc.call<dynamic>('animica_random_epoch');
      return _asInt(e);
    } catch (_) {
      final b = await getBeacon(); // may have epoch inside
      return b.epoch;
    }
  }

  // ---------- Local helpers (preimage/commit) ----------

  /// Create a preimage as sha256(seed || salt). If [saltLen] provided, uses
  /// Random.secure() to produce a salt of that length (default 16 bytes).
  static Uint8List makePreimage(Uint8List seed, {int saltLen = 16}) {
    final salt = Uint8List(saltLen);
    final rnd = Random.secure();
    for (var i = 0; i < saltLen; i++) {
      salt[i] = rnd.nextInt(256);
    }
    final bb = BytesBuilder(copy: false)..add(seed)..add(salt);
    final digest = sha256.convert(bb.toBytes()).bytes;
    return Uint8List.fromList(digest);
  }

  /// sha256(preimage)
  static Uint8List commitmentOf(Uint8List preimage) {
    final d = sha256.convert(preimage).bytes;
    return Uint8List.fromList(d);
  }

  /// Verify that sha256(preimage) == commitment.
  static bool verifyReveal(Uint8List preimage, Uint8List commitment) {
    final got = commitmentOf(preimage);
    return _equals(got, commitment);
  }

  // ---------- Small utils ----------

  static String _asTxAck(dynamic v) {
    // Normalize various node responses to a human-useful string.
    if (v == null) return 'ok';
    if (v is bool) return v ? 'ok' : 'rejected';
    if (v is String) return v; // often a tx hash "0x…"
    if (v is Map) {
      final m = v.map((k, v) => MapEntry(k.toString(), v));
      final hash = m['txHash'] ?? m['hash'] ?? m['id'] ?? m['result'];
      if (hash is String && hash.isNotEmpty) return hash;
      if (m['ok'] == true) return 'ok';
      return jsonEncode(m);
    }
    return v.toString();
  }
}

/// ---------- generic parse/hex helpers ----------

int _asInt(dynamic v) {
  if (v == null) return 0;
  if (v is int) return v;
  final s = v.toString();
  if (s.startsWith('0x') || s.startsWith('0X')) {
    return s.length <= 2 ? 0 : int.parse(s.substring(2), radix: 16);
  }
  return int.tryParse(s) ?? 0;
}

bool _equals(Uint8List a, Uint8List b) {
  if (a.length != b.length) return false;
  var x = 0;
  for (var i = 0; i < a.length; i++) {
    x |= a[i] ^ b[i];
  }
  return x == 0;
}

Uint8List _bytesFromAnyString(String s) {
  final t = s.trim();
  if (t.startsWith('0x') || t.startsWith('0X')) return _bytesFromHex(t);
  // base64?
  try { return Uint8List.fromList(base64.decode(t)); } catch (_) {}
  // utf8 as last resort
  return Uint8List.fromList(utf8.encode(t));
}

Uint8List _bytesFromHex(String hex) {
  var s = hex.startsWith('0x') || hex.startsWith('0X') ? hex.substring(2) : hex;
  if (s.length.isOdd) s = '0$s';
  final out = Uint8List(s.length ~/ 2);
  for (int i = 0; i < s.length; i += 2) {
    out[i ~/ 2] = int.parse(s.substring(i, i + 2), radix: 16);
  }
  return out;
}

String _hex0x(Uint8List b) {
  final sb = StringBuffer('0x');
  for (final x in b) {
    if (x < 16) sb.write('0');
    sb.write(x.toRadixString(16));
  }
  return sb.toString();
}
