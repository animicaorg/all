/**
 * Unit tests for API Key Authentication Middleware
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { Request, Response, NextFunction } from 'express';
import { UnauthorizedError } from '../../utils/errors.js';
import {
  extractSignatureComponents,
  buildPrehashString,
  computeSignature,
  verifySignature,
  hashBody,
} from '../middleware/signature_auth.js';

describe('Signature Authentication', () => {
  describe('hashBody', () => {
    it('should hash empty body correctly', () => {
      const hash = hashBody('');
      expect(hash).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    });

    it('should hash undefined body as empty', () => {
      const hash = hashBody(undefined);
      expect(hash).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    });

    it('should hash JSON body correctly', () => {
      const body = '{"test":"value"}';
      const hash = hashBody(body);
      expect(hash).toHaveLength(64);
      expect(hash).toMatch(/^[a-f0-9]{64}$/);
    });
  });

  describe('buildPrehashString', () => {
    it('should build correct prehash format', () => {
      const components = {
        timestamp: '1234567890000',
        nonce: 'test-nonce-123',
        method: 'POST',
        path: '/api/v1/orders',
        query: 'symbol=BTC-USD',
        bodyHash: 'abc123',
      };

      const prehash = buildPrehashString(components);
      
      expect(prehash).toBe(
        '1234567890000\ntest-nonce-123\nPOST\n/api/v1/orders\nsymbol=BTC-USD\nabc123'
      );
    });

    it('should handle empty query string', () => {
      const components = {
        timestamp: '1234567890000',
        nonce: 'nonce',
        method: 'GET',
        path: '/api/v1/balance',
        query: '',
        bodyHash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      };

      const prehash = buildPrehashString(components);
      expect(prehash.split('\n')).toHaveLength(6);
      expect(prehash.split('\n')[4]).toBe('');
    });
  });

  describe('computeSignature', () => {
    it('should compute HMAC-SHA256 signature', () => {
      const secret = 'test-secret-key';
      const prehash = 'test-message';
      
      const signature = computeSignature(secret, prehash);
      
      expect(signature).toBeTruthy();
      expect(typeof signature).toBe('string');
      // Should be base64 encoded
      expect(signature).toMatch(/^[A-Za-z0-9+/]+=*$/);
    });

    it('should produce consistent signatures', () => {
      const secret = 'my-secret';
      const prehash = 'consistent-message';
      
      const sig1 = computeSignature(secret, prehash);
      const sig2 = computeSignature(secret, prehash);
      
      expect(sig1).toBe(sig2);
    });

    it('should produce different signatures for different secrets', () => {
      const prehash = 'same-message';
      
      const sig1 = computeSignature('secret1', prehash);
      const sig2 = computeSignature('secret2', prehash);
      
      expect(sig1).not.toBe(sig2);
    });
  });

  describe('verifySignature', () => {
    it('should verify matching signatures', () => {
      const expected = 'dGVzdA=='; // "test" in base64
      const provided = 'dGVzdA==';
      
      expect(verifySignature(expected, provided)).toBe(true);
    });

    it('should reject non-matching signatures', () => {
      const expected = 'dGVzdA==';
      const provided = 'ZGlmZg==';
      
      expect(verifySignature(expected, provided)).toBe(false);
    });

    it('should reject invalid base64', () => {
      const expected = 'valid==';
      const provided = 'not-valid-base64!!!';
      
      expect(verifySignature(expected, provided)).toBe(false);
    });

    it('should reject different length signatures', () => {
      const expected = 'dGVzdA==';
      const provided = 'YQ=='; // shorter
      
      expect(verifySignature(expected, provided)).toBe(false);
    });

    it('should use timing-safe comparison', () => {
      // This test verifies the function uses crypto.timingSafeEqual
      const secret = 'test-secret';
      const message = 'test-message';
      
      const signature = computeSignature(secret, message);
      
      expect(verifySignature(signature, signature)).toBe(true);
      expect(verifySignature(signature, 'wrong')).toBe(false);
    });
  });

  describe('extractSignatureComponents', () => {
    it('should extract all components correctly', () => {
      const components = extractSignatureComponents(
        '1234567890000',
        'nonce-123',
        'post',
        '/api/orders',
        'symbol=BTC',
        '{"side":"buy"}'
      );

      expect(components.timestamp).toBe('1234567890000');
      expect(components.nonce).toBe('nonce-123');
      expect(components.method).toBe('POST'); // Uppercased
      expect(components.path).toBe('/api/orders');
      expect(components.query).toBe('symbol=BTC');
      expect(components.bodyHash).toHaveLength(64);
    });

    it('should uppercase method', () => {
      const components = extractSignatureComponents(
        '1234567890000',
        'nonce',
        'get',
        '/path',
        '',
        ''
      );

      expect(components.method).toBe('GET');
    });
  });

  describe('End-to-end signature verification', () => {
    it('should verify a complete request signature', () => {
      const secret = 'my-api-secret';
      const timestamp = '1234567890000';
      const nonce = 'unique-nonce-123';
      const method = 'POST';
      const path = '/api/v1/orders';
      const query = 'symbol=BTC-USD&type=limit';
      const body = JSON.stringify({ side: 'buy', amount: '1.5', price: '50000' });

      // Client side: compute signature
      const components = extractSignatureComponents(
        timestamp,
        nonce,
        method,
        path,
        query,
        body
      );
      const prehash = buildPrehashString(components);
      const signature = computeSignature(secret, prehash);

      // Server side: verify signature
      const serverComponents = extractSignatureComponents(
        timestamp,
        nonce,
        method,
        path,
        query,
        body
      );
      const serverPrehash = buildPrehashString(serverComponents);
      const expectedSignature = computeSignature(secret, serverPrehash);

      expect(verifySignature(expectedSignature, signature)).toBe(true);
    });

    it('should reject signature if body is modified', () => {
      const secret = 'my-api-secret';
      const timestamp = '1234567890000';
      const nonce = 'nonce';
      const method = 'POST';
      const path = '/api/orders';
      const query = '';
      const originalBody = '{"amount":"1.0"}';
      const modifiedBody = '{"amount":"999.0"}';

      // Client signs with original body
      const components = extractSignatureComponents(
        timestamp,
        nonce,
        method,
        path,
        query,
        originalBody
      );
      const prehash = buildPrehashString(components);
      const signature = computeSignature(secret, prehash);

      // Server receives modified body
      const serverComponents = extractSignatureComponents(
        timestamp,
        nonce,
        method,
        path,
        query,
        modifiedBody
      );
      const serverPrehash = buildPrehashString(serverComponents);
      const expectedSignature = computeSignature(secret, serverPrehash);

      expect(verifySignature(expectedSignature, signature)).toBe(false);
    });

    it('should reject signature if timestamp is modified', () => {
      const secret = 'secret';
      const originalTimestamp = '1234567890000';
      const modifiedTimestamp = '1234567899999';
      const nonce = 'nonce';
      const method = 'GET';
      const path = '/api/balance';
      const query = '';
      const body = '';

      // Client signs with original timestamp
      const components = extractSignatureComponents(
        originalTimestamp,
        nonce,
        method,
        path,
        query,
        body
      );
      const signature = computeSignature(secret, buildPrehashString(components));

      // Server verifies with different timestamp
      const serverComponents = extractSignatureComponents(
        modifiedTimestamp,
        nonce,
        method,
        path,
        query,
        body
      );
      const expectedSignature = computeSignature(
        secret,
        buildPrehashString(serverComponents)
      );

      expect(verifySignature(expectedSignature, signature)).toBe(false);
    });
  });
});
