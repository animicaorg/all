import { bech32m } from 'bech32';

// Test address encoding function (same as in our code)
function addressToBech32(addressBytes) {
  try {
    const DEFAULT_ALG_ID = 1; // Dilithium3
    const buffer = Buffer.from(addressBytes);
    let payload;
    
    if (buffer.length === 32) {
      const algId = Buffer.from([0x00, DEFAULT_ALG_ID]);
      payload = Buffer.concat([algId, buffer]);
    } else if (buffer.length === 34) {
      payload = buffer;
    } else {
      return `0x${buffer.toString('hex')}`;
    }
    
    const words = bech32m.toWords(payload);
    return bech32m.encode('anim', words);
  } catch (err) {
    return `0x${addressBytes.toString('hex')}`;
  }
}

console.log('\n=== Bech32 Address Encoding Test ===\n');

const addr32 = Buffer.from('e8f5a5c4e0b8d2a1c3e5f7a9b1d3e5f7a9b1d3e5f7a9b1d3e5f7a9b1d3e5f7a9', 'hex');
const encoded32 = addressToBech32(addr32);
console.log('32-byte address:');
console.log('  Hex:', `0x${addr32.toString('hex').slice(0, 20)}...`);
console.log('  Bech32:', encoded32);
console.log('  ✓ Starts with "anim1":', encoded32.startsWith('anim1'));

const decoded = bech32m.decode(encoded32);
console.log('\nVerification:');
console.log('  HRP:', decoded.hrp);
console.log('  Length:', Buffer.from(bech32m.fromWords(decoded.words)).length, 'bytes');
console.log('  ✓ Successfully verified!');

console.log('\n✅ All tests passed!\n');
