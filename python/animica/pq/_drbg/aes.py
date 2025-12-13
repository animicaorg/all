"""Pure-Python AES-256 implementation for DRBG.

This is a minimal AES-256 implementation in pure Python for use in
deterministic random bit generation (DRBG) for testing only.

Reference: FIPS 197 (Advanced Encryption Standard)

WARNING: This is not optimized for performance and should only be used
for testing. Use native implementations for production.
"""

# AES S-box
SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
]

# Round constants
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _sub_word(word: int) -> int:
    """Apply S-box to each byte of a 4-byte word."""
    return (SBOX[(word >> 24) & 0xff] << 24 |
            SBOX[(word >> 16) & 0xff] << 16 |
            SBOX[(word >> 8) & 0xff] << 8 |
            SBOX[word & 0xff])


def _rot_word(word: int) -> int:
    """Rotate word left by 1 byte."""
    return ((word << 8) | (word >> 24)) & 0xffffffff


def _key_expansion_256(key: bytes) -> list:
    """Expand AES-256 key into round keys.
    
    Args:
        key: 32-byte AES-256 key
    
    Returns:
        List of 60 32-bit round key words (15 round keys of 4 words each)
    """
    if len(key) != 32:
        raise ValueError("AES-256 requires 32-byte key")
    
    # Convert key bytes to words
    w = []
    for i in range(8):  # 8 words for AES-256
        w.append(int.from_bytes(key[i*4:(i+1)*4], 'big'))
    
    # Generate remaining 52 words (total 60 for 14 rounds)
    for i in range(8, 60):
        temp = w[i-1]
        if i % 8 == 0:
            temp = _sub_word(_rot_word(temp)) ^ (RCON[(i // 8) - 1] << 24)
        elif i % 8 == 4:
            temp = _sub_word(temp)
        w.append(w[i-8] ^ temp)
    
    return w


def _add_round_key(state: list, round_key: list):
    """XOR state with round key (in-place)."""
    for i in range(4):
        for j in range(4):
            state[i][j] ^= (round_key[j] >> (24 - 8*i)) & 0xff


def _sub_bytes(state: list):
    """Apply S-box to each byte of state (in-place)."""
    for i in range(4):
        for j in range(4):
            state[i][j] = SBOX[state[i][j]]


def _shift_rows(state: list):
    """Shift rows of state (in-place)."""
    # Row 0: no shift
    # Row 1: shift left by 1
    state[1] = state[1][1:] + state[1][:1]
    # Row 2: shift left by 2
    state[2] = state[2][2:] + state[2][:2]
    # Row 3: shift left by 3
    state[3] = state[3][3:] + state[3][:3]


def _xtime(x: int) -> int:
    """Multiply by x in GF(2^8) with modular reduction."""
    return ((x << 1) ^ (0x1b if x & 0x80 else 0)) & 0xff


def _mix_columns(state: list):
    """Mix columns of state (in-place)."""
    for j in range(4):
        s0, s1, s2, s3 = state[0][j], state[1][j], state[2][j], state[3][j]
        state[0][j] = _xtime(s0) ^ _xtime(s1) ^ s1 ^ s2 ^ s3
        state[1][j] = s0 ^ _xtime(s1) ^ _xtime(s2) ^ s2 ^ s3
        state[2][j] = s0 ^ s1 ^ _xtime(s2) ^ _xtime(s3) ^ s3
        state[3][j] = _xtime(s0) ^ s0 ^ s1 ^ s2 ^ _xtime(s3)


class AES256:
    """Pure-Python AES-256 encryption (ECB mode only)."""
    
    def __init__(self, key: bytes):
        """Initialize with 32-byte key."""
        if len(key) != 32:
            raise ValueError("AES-256 requires 32-byte key")
        self._round_keys = _key_expansion_256(key)
    
    def encrypt_block(self, plaintext: bytes) -> bytes:
        """Encrypt a single 16-byte block.
        
        Args:
            plaintext: 16-byte block
        
        Returns:
            16-byte ciphertext block
        """
        if len(plaintext) != 16:
            raise ValueError("Block must be 16 bytes")
        
        # Convert to state matrix (column-major)
        state = [[plaintext[i + 4*j] for j in range(4)] for i in range(4)]
        
        # Initial round
        round_key = self._round_keys[0:4]
        _add_round_key(state, round_key)
        
        # Main rounds (1-13 for AES-256)
        for round_num in range(1, 14):
            _sub_bytes(state)
            _shift_rows(state)
            _mix_columns(state)
            round_key = self._round_keys[round_num*4:(round_num+1)*4]
            _add_round_key(state, round_key)
        
        # Final round (no MixColumns)
        _sub_bytes(state)
        _shift_rows(state)
        round_key = self._round_keys[14*4:15*4]
        _add_round_key(state, round_key)
        
        # Convert state back to bytes (column-major)
        ciphertext = bytes(state[i][j] for j in range(4) for i in range(4))
        return ciphertext


def aes256_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt plaintext using AES-256 ECB mode.
    
    Args:
        key: 32-byte key
        plaintext: Data to encrypt (must be multiple of 16 bytes)
    
    Returns:
        Encrypted ciphertext
    """
    if len(plaintext) % 16 != 0:
        raise ValueError("Plaintext must be multiple of 16 bytes")
    
    cipher = AES256(key)
    ciphertext = b""
    for i in range(0, len(plaintext), 16):
        block = plaintext[i:i+16]
        ciphertext += cipher.encrypt_block(block)
    
    return ciphertext


# Quick self-test
if __name__ == "__main__":
    # Test vector from FIPS 197
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
    expected = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    
    cipher = AES256(key)
    result = cipher.encrypt_block(plaintext)
    
    assert result == expected, f"AES-256 test failed: {result.hex()} != {expected.hex()}"
    print("AES-256 self-test passed")
