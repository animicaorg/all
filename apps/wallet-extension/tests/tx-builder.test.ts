import { describe, it, expect } from 'vitest';
import { buildAndSignTransfer } from '../src/core/tx/builder';
import { addressFromPubkey } from '../src/core/crypto/address';
import type { TxParams } from '../src/core/tx/builder';

describe('tx builder validation', () => {
  // Create valid addresses
  const fromPubkey = new Uint8Array(1952); // Dilithium3 size
  const toPubkey = new Uint8Array(1952);
  for (let i = 0; i < fromPubkey.length; i++) {
    fromPubkey[i] = (i * 3) % 256;
    toPubkey[i] = (i * 5) % 256;
  }
  
  const fromAddress = addressFromPubkey(fromPubkey, 0x1001);
  const toAddress = addressFromPubkey(toPubkey, 0x1001);

  const validParams: TxParams = {
    chainId: 1337,
    from: fromAddress,
    to: toAddress,
    amount: 1000000000, // 1 ANM
    gasPrice: 1000000,
    gasLimit: 21000,
    validAfter: 0,
    validUntil: 120,
  };

  const validSecretKey = new Uint8Array(4000); // Dilithium3 size
  const validPublicKey = new Uint8Array(1952); // Dilithium3 size
  const validAlgId = 0x1001; // DILITHIUM3_ALG_ID

  // Fill with some data so it's not all zeros
  for (let i = 0; i < validSecretKey.length; i++) {
    validSecretKey[i] = i % 256;
  }
  for (let i = 0; i < validPublicKey.length; i++) {
    validPublicKey[i] = (i * 2) % 256;
  }

  describe('valid inputs', () => {
    it('should build and sign transaction with valid inputs', async () => {
      const result = await buildAndSignTransfer(
        validParams,
        validSecretKey,
        validPublicKey,
        validAlgId
      );

      expect(result).toBeDefined();
      expect(result.txid).toBeDefined();
      expect(typeof result.txid).toBe('string');
      expect(result.txid.length).toBeGreaterThan(0);
      expect(result.unsignedHash).toBeDefined();
      expect(typeof result.unsignedHash).toBe('string');
      expect(result.signedTx).toBeDefined();
    });
  });

  describe('invalid secretKey', () => {
    it('should throw on undefined secretKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, undefined as any, validPublicKey, validAlgId)
      ).rejects.toThrow('secretKey is required for signing');
    });

    it('should throw on empty secretKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, new Uint8Array(0), validPublicKey, validAlgId)
      ).rejects.toThrow('secretKey is required for signing');
    });

    it('should throw on null secretKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, null as any, validPublicKey, validAlgId)
      ).rejects.toThrow('secretKey is required for signing');
    });
  });

  describe('invalid publicKey', () => {
    it('should throw on undefined publicKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, validSecretKey, undefined as any, validAlgId)
      ).rejects.toThrow('publicKey is required for signing');
    });

    it('should throw on empty publicKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, validSecretKey, new Uint8Array(0), validAlgId)
      ).rejects.toThrow('publicKey is required for signing');
    });

    it('should throw on null publicKey', async () => {
      await expect(
        buildAndSignTransfer(validParams, validSecretKey, null as any, validAlgId)
      ).rejects.toThrow('publicKey is required for signing');
    });
  });

  describe('invalid addresses', () => {
    it('should throw on empty from address', async () => {
      const params = { ...validParams, from: '' };
      await expect(
        buildAndSignTransfer(params, validSecretKey, validPublicKey, validAlgId)
      ).rejects.toThrow('from address is required');
    });

    it('should throw on undefined from address', async () => {
      const params = { ...validParams, from: undefined as any };
      await expect(
        buildAndSignTransfer(params, validSecretKey, validPublicKey, validAlgId)
      ).rejects.toThrow('from address is required');
    });

    it('should throw on empty to address', async () => {
      const params = { ...validParams, to: '' };
      await expect(
        buildAndSignTransfer(params, validSecretKey, validPublicKey, validAlgId)
      ).rejects.toThrow('to address is required');
    });

    it('should throw on undefined to address', async () => {
      const params = { ...validParams, to: undefined as any };
      await expect(
        buildAndSignTransfer(params, validSecretKey, validPublicKey, validAlgId)
      ).rejects.toThrow('to address is required');
    });
  });

  describe('regression: undefined.slice crash prevention', () => {
    it('should not crash with "cannot read .slice of undefined" on missing secretKey', async () => {
      let error: Error | undefined;
      try {
        await buildAndSignTransfer(validParams, undefined as any, validPublicKey, validAlgId);
      } catch (e) {
        error = e as Error;
      }
      
      expect(error).toBeDefined();
      expect(error!.message).not.toContain('slice');
      expect(error!.message).toContain('secretKey');
    });

    it('should not crash with "cannot read .slice of undefined" on missing publicKey', async () => {
      let error: Error | undefined;
      try {
        await buildAndSignTransfer(validParams, validSecretKey, undefined as any, validAlgId);
      } catch (e) {
        error = e as Error;
      }
      
      expect(error).toBeDefined();
      expect(error!.message).not.toContain('slice');
      expect(error!.message).toContain('publicKey');
    });
  });
});
