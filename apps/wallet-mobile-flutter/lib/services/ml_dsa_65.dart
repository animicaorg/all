// Real FIPS 204 ML-DSA-65 (alg_id 0x1003) for the mobile wallet.
//
// Implementation strategy
// -----------------------
// No pure-Dart FIPS 204 ML-DSA package on pub.dev is byte-compatible with
// the chain's verifier (the closest, `dilithium_crypto`, ships pre-FIPS
// Dilithium round-3 with sk=4000/sig=3293 — the chain expects FIPS 204
// sk=4032/sig=3309). Porting noble's ~1250-line TS impl to Dart is a
// multi-day exercise.
//
// Pragmatic alternative: bundle the same `@noble/post-quantum` JS that
// the browser extension uses (40 KB IIFE, MIT-licensed, pure-JS no
// native deps) and run it via `flutter_js`. On iOS this runs in
// JavaScriptCore, on Android in QuickJS. Both are sandboxed JS
// interpreters with no network or filesystem access — they're safe to
// feed user secret keys.
//
// Trade-offs vs a hypothetical native Dart port:
//   + Byte-identical to the extension + chain (same source code).
//   + Spec correctness is whatever noble has — no porting bugs.
//   - First call pays ~50 ms JS engine warm-up; sign is then ~10 ms.
//   - +~5 MB to the app binary for the JS engine.
//
// PQ policy compliance: noble is vendored as an app asset, not pip/npm-
// installed at runtime, mirroring how the node vendors jack4818's
// dilithium_py at python/animica/_vendor/dilithium_py_v2/.

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;
import 'package:flutter_js/flutter_js.dart';

class MlDsa65Keypair {
  final Uint8List publicKey;   // 1952 bytes
  final Uint8List secretKey;   // 4032 bytes
  const MlDsa65Keypair(this.publicKey, this.secretKey);
}

class MlDsa65 {
  static const int publicKeyLen = 1952;
  static const int secretKeyLen = 4032;
  static const int signatureLen = 3309;
  static const int seedLen = 32;
  static const String assetPath = 'assets/js/ml_dsa_65.bundle.js';

  static JavascriptRuntime? _rt;
  static Future<void>? _initFuture;

  /// Initialise the JS runtime + load the noble bundle. Idempotent.
  /// First call is the slow one (~50-100 ms on a mid-range phone).
  static Future<void> _ensureReady() async {
    if (_rt != null) return;
    if (_initFuture != null) return _initFuture;
    _initFuture = _initialise();
    return _initFuture;
  }

  static Future<void> _initialise() async {
    final rt = getJavascriptRuntime();
    final bundle = await rootBundle.loadString(assetPath);
    final res = rt.evaluate(bundle);
    if (res.isError) {
      throw StateError('ml_dsa_65 bundle eval failed: ${res.stringResult}');
    }
    // Stash the API in a stable global the wrappers below call into.
    // The IIFE bundle exposes `NobleMlDsa.ml_dsa65` already.
    final probe = rt.evaluate('typeof NobleMlDsa');
    if (probe.stringResult != 'object') {
      throw StateError(
          'ml_dsa_65 bundle did not expose NobleMlDsa global (got: ${probe.stringResult})');
    }
    // Some QuickJS builds don't ship a crypto.getRandomValues — noble's
    // keygen takes an explicit seed so we never call into the engine's
    // RNG. (Sign uses sk-derived randomness, also seed-free.)
    _rt = rt;
  }

  /// Generate a fresh ML-DSA-65 keypair from `seed` (32 bytes). The
  /// caller is responsible for sourcing seed bytes from a CSPRNG —
  /// typically `Random.secure()` or `flutter_secure_storage` material.
  static Future<MlDsa65Keypair> keygen(Uint8List seed) async {
    if (seed.length != seedLen) {
      throw ArgumentError('seed must be $seedLen bytes, got ${seed.length}');
    }
    await _ensureReady();
    final rt = _rt!;
    final seedB64 = base64Encode(seed);
    // Run keygen and pack both keys as base64 strings (avoids the
    // flutter_js Uint8Array marshaling pitfalls — strings round-trip
    // cleanly through every backend).
    final js = '''
      (function() {
        var seed = Uint8Array.from(atob("$seedB64"), c => c.charCodeAt(0));
        var kp = NobleMlDsa.ml_dsa65.keygen(seed);
        function _b64(u8) {
          var s = '';
          for (var i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
          return btoa(s);
        }
        return JSON.stringify({pk: _b64(kp.publicKey), sk: _b64(kp.secretKey)});
      })()
    ''';
    final res = rt.evaluate(js);
    if (res.isError) {
      throw StateError('ml_dsa_65 keygen failed: ${res.stringResult}');
    }
    final parsed = jsonDecode(res.stringResult) as Map<String, dynamic>;
    final pk = base64Decode(parsed['pk'] as String);
    final sk = base64Decode(parsed['sk'] as String);
    if (pk.length != publicKeyLen || sk.length != secretKeyLen) {
      throw StateError(
          'ml_dsa_65 keygen size mismatch: pk=${pk.length}, sk=${sk.length}');
    }
    return MlDsa65Keypair(Uint8List.fromList(pk), Uint8List.fromList(sk));
  }

  /// Sign `message` with `secretKey`. Returns the 3309-byte FIPS 204
  /// signature the chain's pq.py.algs.ml_dsa_65 verifier (vendored
  /// jack4818/dilithium-py) accepts byte-for-byte.
  ///
  /// `message` is whatever the caller has prehashed / framed; this
  /// signs the bytes verbatim with no extra domain prefix. The
  /// `signer.dart:buildSignBytes` helper produces the right bytes
  /// for the chain's canonical sign-bytes layout.
  static Future<Uint8List> sign(Uint8List secretKey, Uint8List message) async {
    if (secretKey.length != secretKeyLen) {
      throw ArgumentError(
          'secretKey must be $secretKeyLen bytes, got ${secretKey.length}');
    }
    await _ensureReady();
    final rt = _rt!;
    final skB64 = base64Encode(secretKey);
    final msgB64 = base64Encode(message);
    final js = '''
      (function() {
        var sk = Uint8Array.from(atob("$skB64"), c => c.charCodeAt(0));
        var msg = Uint8Array.from(atob("$msgB64"), c => c.charCodeAt(0));
        var sig = NobleMlDsa.ml_dsa65.sign(msg, sk);
        var s = '';
        for (var i = 0; i < sig.length; i++) s += String.fromCharCode(sig[i]);
        return btoa(s);
      })()
    ''';
    final res = rt.evaluate(js);
    if (res.isError) {
      throw StateError('ml_dsa_65 sign failed: ${res.stringResult}');
    }
    final sig = base64Decode(res.stringResult);
    if (sig.length != signatureLen) {
      throw StateError('ml_dsa_65 sign size mismatch: got ${sig.length}');
    }
    return Uint8List.fromList(sig);
  }

  /// Verify `signature` over `message` under `publicKey`. Returns false
  /// (never throws) on any malformed input so the caller can flag the
  /// envelope as bad without needing try/catch around every check.
  static Future<bool> verify(
    Uint8List publicKey,
    Uint8List message,
    Uint8List signature,
  ) async {
    if (publicKey.length != publicKeyLen || signature.length != signatureLen) {
      return false;
    }
    await _ensureReady();
    final rt = _rt!;
    final pkB64 = base64Encode(publicKey);
    final msgB64 = base64Encode(message);
    final sigB64 = base64Encode(signature);
    final js = '''
      (function() {
        try {
          var pk = Uint8Array.from(atob("$pkB64"), c => c.charCodeAt(0));
          var msg = Uint8Array.from(atob("$msgB64"), c => c.charCodeAt(0));
          var sig = Uint8Array.from(atob("$sigB64"), c => c.charCodeAt(0));
          return NobleMlDsa.ml_dsa65.verify(sig, msg, pk) ? "1" : "0";
        } catch (e) { return "0"; }
      })()
    ''';
    final res = rt.evaluate(js);
    if (res.isError) return false;
    return res.stringResult == '1';
  }
}
