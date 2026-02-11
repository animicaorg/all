import { describe, it, expect } from 'vitest';
import { encrypt, decrypt } from '../src/core/crypto/vault';

describe('Vault Encryption', () => {
  it('should encrypt and decrypt data correctly', async () => {
    const password = 'test-password-12345';
    const data = JSON.stringify({ test: 'data', number: 42 });

    const encrypted = await encrypt(data, password);

    expect(encrypted.salt).toBeDefined();
    expect(encrypted.iv).toBeDefined();
    expect(encrypted.ciphertext).toBeDefined();

    const decrypted = await decrypt(
      encrypted.salt,
      encrypted.iv,
      encrypted.ciphertext,
      password
    );

    expect(decrypted).toBe(data);
  });

  it('should fail with wrong password', async () => {
    const password = 'correct-password';
    const wrongPassword = 'wrong-password';
    const data = 'secret data';

    const encrypted = await encrypt(data, password);

    await expect(
      decrypt(encrypted.salt, encrypted.iv, encrypted.ciphertext, wrongPassword)
    ).rejects.toThrow();
  });

  it('should use different salts and IVs for each encryption', async () => {
    const password = 'test-password';
    const data = 'same data';

    const encrypted1 = await encrypt(data, password);
    const encrypted2 = await encrypt(data, password);

    expect(encrypted1.salt).not.toBe(encrypted2.salt);
    expect(encrypted1.iv).not.toBe(encrypted2.iv);
    expect(encrypted1.ciphertext).not.toBe(encrypted2.ciphertext);
  });
});
