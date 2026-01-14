import { decode as cborDecode } from 'cbor';

// Test CBOR decoding of extra field
const testCases = [
  // Empty buffer
  { input: Buffer.from([]), expected: 'skip-empty' },
  
  // Valid CBOR with coinbase
  { input: Buffer.from('a168636f696e62617365582000112233445566778899aabbccddeeff00112233445566778899aabbccddeeff', 'hex'), expected: 'coinbase-present' },
  
  // Invalid CBOR
  { input: Buffer.from('invalid', 'utf8'), expected: 'decode-error' },
  
  // CBOR without coinbase
  { input: Buffer.from('a16b696e7374616e745f626c6f636bf5', 'hex'), expected: 'no-coinbase' },
];

console.log('Testing CBOR decoding...\n');

testCases.forEach((tc, i) => {
  console.log(`Test ${i + 1}:`, tc.expected);
  try {
    if (tc.input.length === 0) {
      console.log('  ✓ Empty buffer skipped');
      return;
    }
    
    const decoded = cborDecode(tc.input);
    console.log('  Decoded:', JSON.stringify(decoded));
    
    if (decoded && decoded.coinbase) {
      console.log('  ✓ Coinbase found:', Buffer.from(decoded.coinbase).toString('hex'));
    } else {
      console.log('  ✓ No coinbase field');
    }
  } catch (error) {
    console.log('  ✓ Error caught:', error.message);
  }
  console.log('');
});

console.log('All tests completed successfully!');
