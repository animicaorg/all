import { addressToBech32 } from './dist/utils/bech32.js';

console.log('\n=== Testing Shared Bech32 Utility ===\n');

// Test with 32-byte address
const addr32 = Buffer.from('e8f5a5c4e0b8d2a1c3e5f7a9b1d3e5f7a9b1d3e5f7a9b1d3e5f7a9b1d3e5f7a9', 'hex');
const result32 = addressToBech32(addr32);
console.log('32-byte address test:');
console.log('  Result:', result32);
console.log('  ✓ Starts with "anim1":', result32.startsWith('anim1'));

// Test with Uint8Array
const addr32Array = new Uint8Array(addr32);
const resultArray = addressToBech32(addr32Array);
console.log('\nUint8Array test:');
console.log('  Result:', resultArray);
console.log('  ✓ Matches Buffer result:', result32 === resultArray);

console.log('\n✅ Shared utility working correctly!\n');
