/*
 * Animica Wallet — SHA3 / Keccak Wrappers
 *
 * Provides:
 *   - sha3_256(bytes)    → Uint8List (32 bytes)
 *   - keccak256(bytes)   → Uint8List (32 bytes, legacy pre-SHA3 padding)
 *   - hexSha3_256(bytes) → String (0x…)
 *   - hexKeccak256(bytes)→ String (0x…)
 *   - sha256(bytes)      → Uint8List (via package:crypto) — convenience
 *
 * NOTE:
 *   We implement Keccak-f[1600] locally for portability (no extra deps).
 *   The only external dependency used here is package:crypto for SHA-256.
 */

import 'dart:typed_data';
import 'dart:convert' show utf8;
import 'package:crypto/crypto.dart' as crypto show sha256;

const _MASK64 = 0xFFFFFFFFFFFFFFFF;

/// Public helpers -------------------------------------------------------------

Uint8List sha3_256(List<int> data) =>
    _Keccak.sponge(Uint8List.fromList(data), rateBytes: 136, outLen: 32, domainSuffix: 0x06);

Uint8List keccak256(List<int> data) =>
    _Keccak.sponge(Uint8List.fromList(data), rateBytes: 136, outLen: 32, domainSuffix: 0x01);

String hexSha3_256(List<int> data, {bool with0x = true}) =>
    _toHex(sha3_256(data), with0x: with0x);

String hexKeccak256(List<int> data, {bool with0x = true}) =>
    _toHex(keccak256(data), with0x: with0x);

/// Convenience: SHA-256 via package:crypto (sometimes handy for checksums)
Uint8List sha256(List<int> data) =>
    Uint8List.fromList(crypto.sha256.convert(data).bytes);

/// Small helpers --------------------------------------------------------------

Uint8List sha3_256Utf8(String s) => sha3_256(utf8.encode(s));
Uint8List keccak256Utf8(String s) => keccak256(utf8.encode(s));
String hexSha3_256Utf8(String s, {bool with0x = true}) =>
    hexSha3_256(utf8.encode(s), with0x: with0x);
String hexKeccak256Utf8(String s, {bool with0x = true}) =>
    hexKeccak256(utf8.encode(s), with0x: with0x);

String _toHex(List<int> bytes, {bool with0x = true}) {
  final sb = StringBuffer();
  if (with0x) sb.write('0x');
  const hexd = '0123456789abcdef';
  for (final b in bytes) {
    sb.write(hexd[(b >> 4) & 0xF]);
    sb.write(hexd[b & 0xF]);
  }
  return sb.toString();
}

/// Keccak-f[1600] + sponge (SHA3 / Keccak) -----------------------------------

class _Keccak {
  // Rotation offsets (rho), indexed by lane (x + 5*y)
  static const List<int> _R = <int>[
    // y = 0
     0,  1, 62, 28, 27,
    // y = 1
    36, 44,  6, 55, 20,
    // y = 2
     3, 10, 43, 25, 39,
    // y = 3
    41, 45, 15, 21,  8,
    // y = 4
    18,  2, 61, 56, 14,
  ];

  // Round constants
  static const List<int> _RC = <int>[
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808a,
    0x8000000080008000,
    0x000000000000808b,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008a,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000a,
    0x000000008000808b,
    0x800000000000008b,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800a,
    0x800000008000000a,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
  ];

  /// Sponge construction.
  /// - [rateBytes] for SHA3-256/Keccak-256 is 136.
  /// - [domainSuffix] 0x06 for SHA3, 0x01 for Keccak (legacy).
  static Uint8List sponge(Uint8List msg,
      {required int rateBytes, required int outLen, required int domainSuffix}) {
    // State A as 25 lanes of 64 bits
    final A = List<int>.filled(25, 0);

    // Absorb full blocks
    int off = 0;
    while (off + rateBytes <= msg.length) {
      _xorBlockIntoState(A, msg, off, rateBytes);
      _permute(A);
      off += rateBytes;
    }

    // Final block with domain separation + padding
    final last = Uint8List(rateBytes);
    final rem = msg.length - off;
    if (rem > 0) {
      last.setRange(0, rem, msg, off);
    }
    last[rem] ^= domainSuffix;            // domain separation
    last[rateBytes - 1] ^= 0x80;          // final bit (multi-rate padding)

    _xorBlockIntoState(A, last, 0, rateBytes);
    _permute(A);

    // Squeeze
    final out = Uint8List(outLen);
    int outOff = 0;
    while (outOff < outLen) {
      final take = (outLen - outOff < rateBytes) ? (outLen - outOff) : rateBytes;
      _stateToBytes(A, out, outOff, take);
      outOff += take;
      if (outOff < outLen) _permute(A);
    }
    return out;
  }

  /// XOR a rate-sized block (little-endian lanes) into the state.
  static void _xorBlockIntoState(List<int> A, Uint8List block, int off, int len) {
    // len is multiple of 8 except final usage in squeeze (here it's always full rate)
    final laneCount = len ~/ 8;
    for (int i = 0; i < laneCount; i++) {
      final v = _le64(block, off + 8 * i);
      A[i] = (A[i] ^ v) & _MASK64;
    }
    // If len not multiple of 8 (shouldn't happen in absorb), ignore tail.
  }

  /// Write `take` bytes from state into `out` (little-endian per lane).
  static void _stateToBytes(List<int> A, Uint8List out, int outOff, int take) {
    int i = 0;
    int written = 0;
    while (written + 8 <= take) {
      _storeLe64(A[i] & _MASK64, out, outOff + written);
      written += 8;
      i++;
    }
    if (written < take) {
      // partial lane
      final tmp = Uint8List(8);
      _storeLe64(A[i] & _MASK64, tmp, 0);
      out.setRange(outOff + written, outOff + take, tmp);
    }
  }

  /// Keccak-f[1600] permutation (24 rounds).
  static void _permute(List<int> a) {
    for (int round = 0; round < 24; round++) {
      // θ step
      final c = List<int>.filled(5, 0);
      for (int x = 0; x < 5; x++) {
        c[x] = (a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20]) & _MASK64;
      }
      final d = List<int>.filled(5, 0);
      for (int x = 0; x < 5; x++) {
        d[x] = (c[(x + 4) % 5] ^ _rotl64(c[(x + 1) % 5], 1)) & _MASK64;
      }
      for (int y = 0; y < 5; y++) {
        final y5 = 5 * y;
        for (int x = 0; x < 5; x++) {
          a[x + y5] = (a[x + y5] ^ d[x]) & _MASK64;
        }
      }

      // ρ and π steps combined
      final b = List<int>.filled(25, 0);
      for (int y = 0; y < 5; y++) {
        for (int x = 0; x < 5; x++) {
          final idx = x + 5 * y;
          final rot = _R[idx];
          final newX = y;
          final newY = (2 * x + 3 * y) % 5;
          b[newX + 5 * newY] = _rotl64(a[idx], rot);
        }
      }

      // χ step
      for (int y = 0; y < 5; y++) {
        final y5 = 5 * y;
        final b0 = b[0 + y5];
        final b1 = b[1 + y5];
        final b2 = b[2 + y5];
        final b3 = b[3 + y5];
        final b4 = b[4 + y5];
        a[0 + y5] = (b0 ^ ((~b1) & b2)) & _MASK64;
        a[1 + y5] = (b1 ^ ((~b2) & b3)) & _MASK64;
        a[2 + y5] = (b2 ^ ((~b3) & b4)) & _MASK64;
        a[3 + y5] = (b3 ^ ((~b4) & b0)) & _MASK64;
        a[4 + y5] = (b4 ^ ((~b0) & b1)) & _MASK64;
      }

      // ι step
      a[0] = (a[0] ^ _RC[round]) & _MASK64;
    }
  }

  /// Little-endian load/store of 64-bit words.
  static int _le64(Uint8List bs, int off) {
    // Assemble as unsigned 64-bit
    int v = 0;
    v |= bs[off + 0];
    v |= bs[off + 1] << 8;
    v |= bs[off + 2] << 16;
    v |= bs[off + 3] << 24;
    v |= bs[off + 4] << 32;
    v |= bs[off + 5] << 40;
    v |= bs[off + 6] << 48;
    v |= bs[off + 7] << 56;
    return v & _MASK64;
  }

  static void _storeLe64(int v, Uint8List out, int off) {
    out[off + 0] = v & 0xFF;
    out[off + 1] = (v >>> 8) & 0xFF;
    out[off + 2] = (v >>> 16) & 0xFF;
    out[off + 3] = (v >>> 24) & 0xFF;
    out[off + 4] = (v >>> 32) & 0xFF;
    out[off + 5] = (v >>> 40) & 0xFF;
    out[off + 6] = (v >>> 48) & 0xFF;
    out[off + 7] = (v >>> 56) & 0xFF;
  }

  static int _rotl64(int x, int n) {
    final v = x & _MASK64;
    final s = n & 63;
    return (((v << s) & _MASK64) | (v >>> (64 - s))) & _MASK64;
  }
}
