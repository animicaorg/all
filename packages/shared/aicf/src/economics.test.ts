import { describe, expect, it } from 'vitest';
import { calculateCharge } from './economics.js';
import { getModelDefinition } from './models.js';

describe('AICF economics', () => {
  it('calculates charge split and subsidy', () => {
    const model = getModelDefinition('aicf-chat-1');
    const charge = calculateCharge({
      pricing: model.pricing,
      inputTokens: 100,
      outputTokens: 120,
      subsidyBps: 500
    });

    expect(charge.grossChargeAnmNanos).toBeGreaterThan(0n);
    expect(charge.netChargeAnmNanos).toBeLessThan(charge.grossChargeAnmNanos);
    expect(charge.providerRewardAnmNanos + charge.treasuryCutAnmNanos).toEqual(charge.netChargeAnmNanos);
  });
});
