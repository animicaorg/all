import { describe, it, expect } from 'vitest';
import { formatANM } from '../src/services/balances';

describe('formatANM', () => {
  it('formats zero with nine decimals', () => {
    expect(formatANM(0n)).toBe('0.000000000');
  });

  it('formats full ANM values', () => {
    expect(formatANM('12000000000')).toBe('12.000000000');
  });

  it('adds grouping separators for large ANM values', () => {
    expect(formatANM('1234567890123456789')).toBe('1,234,567,890.123456789');
  });

  it('formats fractional ANM values', () => {
    expect(formatANM('12345678901')).toBe('12.345678901');
  });
});
