/*
 * Animica Wallet — Mnemonic (BIP39-like with SHA3 PBKDF/HKDF)
 *
 * Uses FULL 2048-word BIP-0039 English list from wordlist_en.dart.
 * Differences vs. BIP39:
 *  • ENT checksum: SHA3-256
 *  • Seed derivation: PBKDF2-HMAC-SHA3-512 (2048 iters)
 *  • Optional HKDF-SHA3-256 expand for sub-keys
 */

import 'dart:convert' show utf8;
import 'dart:math';
import 'dart:typed_data';

import '../crypto/sha3.dart' as sha3;
import 'wordlist_en.dart' show bip39English;

class Mnemonic {
  static List<String> _wordlist = List.unmodifiable(bip39English);

  static void setWordlist(List<String> words) {
    _validateWordlist(words);
    _wordlist = List.unmodifiable(words);
  }

  static List<String> get wordlist => _wordlist;

  static String generate({int strength = 256, Random? rng}) {
    _assertStrength(strength);
    final entropy = _randomBytes(strength ~/ 8, rng: rng);
    return entropyToMnemonic(entropy);
  }

  static String entropyToMnemonic(Uint8List entropy) {
    _assertStrength(entropy.length * 8);
    final bits = _bytesToBits(entropy);
    final csLen = entropy.length * 8 ~/ 32;
    final checksum = _checksumBits(entropy, csLen);
    final full = bits + checksum;

    final words = <String>[];
    for (var i = 0; i < full.length; i += 11) {
      final idx = int.parse(full.substring(i, i + 11), radix: 2);
      words.add(_wordlist[idx]);
    }
    return words.join(' ');
  }

  static Uint8List mnemonicToEntropy(String mnemonic) {
    final parts = mnemonic.trim().split(RegExp(r'\s+'));
    if (parts.length % 3 != 0) {
      throw ArgumentError('Mnemonic word count must be divisible by 3 (got ${parts.length})');
    }
    final bitStr = StringBuffer();
    for (final w in parts) {
      final idx = _wordlist.indexOf(w);
      if (idx < 0) throw ArgumentError('Unknown mnemonic word: "$w"');
      bitStr.write(idx.toRadixString(2).padLeft(11, '0'));
    }
    final full = bitStr.toString();
    final entLen = (full.length / 33 * 32).round();
    final csLen = full.length - entLen;
    if (csLen <= 0 || entLen <= 0 || entLen % 8 != 0) {
      throw ArgumentError('Invalid mnemonic bit lengths');
    }
    final entropy = _bitsToBytes(full.substring(0, entLen));
    final want = _checksumBits(entropy, csLen);
    if (full.substring(entLen) != want) throw ArgumentError('Mnemonic checksum mismatch');
    return entropy;
  }

  static Uint8List mnemonicToSeed(String mnemonic, {String passphrase = ''}) {
    final password = Uint8List.fromList(utf8.encode(_nfkd(mnemonic)));
    final salt = Uint8List.fromList(utf8.encode('mnemonic${_nfkd(passphrase)}'));
    return _pbkdf2HmacSha3_512(password, salt, 2048, 64);
  }

  static Uint8List seedHkdfExpand({
    required Uint8List ikm,
    Uint8List? salt,
    required List<int> info,
    int length = 32,
  }) {
    return _hkdfSha3_256(ikm: ikm, salt: salt, info: Uint8List.fromList(info), length: length);
  }
}

// Internals

void _assertStrength(int strengthBits) {
  const allowed = {128, 160, 192, 224, 256};
  if (!allowed.contains(strengthBits)) {
    throw ArgumentError('Strength must be one of {128,160,192,224,256}, got $strengthBits');
  }
}

void _validateWordlist(List<String> words) {
  if (words.length != 2048) throw ArgumentError('Wordlist must contain exactly 2048 words');
  final set = <String>{};
  for (final w in words) {
    if (!set.add(w)) throw ArgumentError('Wordlist contains duplicates (e.g., "$w")');
  }
}

Uint8List _randomBytes(int n, {Random? rng}) {
  final r = rng ?? Random.secure();
  final out = Uint8List(n);
  for (var i = 0; i < n; i++) out[i] = r.nextInt(256);
  return out;
}

String _bytesToBits(Uint8List xs) {
  final sb = StringBuffer();
  for (final b in xs) sb.write(b.toRadixString(2).padLeft(8, '0'));
  return sb.toString();
}

Uint8List _bitsToBytes(String bits) {
  final out = Uint8List(bits.length ~/ 8);
  for (int i = 0, j = 0; i < bits.length; i += 8, j++) {
    out[j] = int.parse(bits.substring(i, i + 8), radix: 2);
  }
  return out;
}

String _checksumBits(Uint8List entropy, int csLen) {
  final h = sha3.sha3_256(entropy);
  final bits = _bytesToBits(h);
  return bits.substring(0, csLen);
}

String _nfkd(String s) => s; // stub

Uint8List _hmacSha3_512(Uint8List key, List<int> msg) {
  const blockSize = 72; // SHA3-512 rate
  var k = key;
  if (k.length > blockSize) k = sha3.sha3_512(k);
  if (k.length < blockSize) {
    final kk = Uint8List(blockSize)..setAll(0, k);
    k = kk;
  }
  final oKeyPad = Uint8List(blockSize), iKeyPad = Uint8List(blockSize);
  for (var i = 0; i < blockSize; i++) {
    oKeyPad[i] = 0x5c ^ k[i];
    iKeyPad[i] = 0x36 ^ k[i];
  }
  final inner = sha3.sha3_512(Uint8List.fromList([...iKeyPad, ...msg]));
  final outer = sha3.sha3_512(Uint8List.fromList([...oKeyPad, ...inner]));
  return outer;
}

Uint8List _hmacSha3_256(Uint8List key, List<int> msg) {
  const blockSize = 136; // SHA3-256 rate
  var k = key;
  if (k.length > blockSize) k = sha3.sha3_256(k);
  if (k.length < blockSize) {
    final kk = Uint8List(blockSize)..setAll(0, k);
    k = kk;
  }
  final oKeyPad = Uint8List(blockSize), iKeyPad = Uint8List(blockSize);
  for (var i = 0; i < blockSize; i++) {
    oKeyPad[i] = 0x5c ^ k[i];
    iKeyPad[i] = 0x36 ^ k[i];
  }
  final inner = sha3.sha3_256(Uint8List.fromList([...iKeyPad, ...msg]));
  final outer = sha3.sha3_256(Uint8List.fromList([...oKeyPad, ...inner]));
  return outer;
}

Uint8List _pbkdf2HmacSha3_512(Uint8List password, Uint8List salt, int iterations, int dkLen) {
  final hLen = 64;
  final l = (dkLen / hLen).ceil();
  final out = BytesBuilder();
  for (var i = 1; i <= l; i++) {
    final block = Uint8List.fromList([...salt, (i >> 24) & 0xff, (i >> 16) & 0xff, (i >> 8) & 0xff, i & 0xff]);
    var u = _hmacSha3_512(password, block);
    var t = Uint8List.fromList(u);
    for (var j = 2; j <= iterations; j++) {
      u = _hmacSha3_512(password, u);
      for (var k = 0; k < t.length; k++) t[k] ^= u[k];
    }
    out.add(t);
  }
  final dk = out.toBytes();
  return Uint8List.sublistView(dk, 0, dkLen);
}

Uint8List _hkdfSha3_256({
  required Uint8List ikm,
  Uint8List? salt,
  required Uint8List info,
  int length = 32,
}) {
  final zeroSalt = Uint8List(0);
  final prk = _hmacSha3_256(salt ?? zeroSalt, ikm);
  final n = (length / 32).ceil();
  final okm = BytesBuilder();
  var prev = Uint8List(0);
  for (var i = 1; i <= n; i++) {
    final t = _hmacSha3_256(prk, [...prev, ...info, i]);
    okm.add(t);
    prev = t;
  }
  final out = okm.toBytes();
  return Uint8List.sublistView(out, 0, length);
}
