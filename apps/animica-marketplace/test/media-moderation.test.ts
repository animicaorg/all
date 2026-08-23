import test from 'node:test';
import assert from 'node:assert/strict';
import { moderateMediaPrompt } from '../lib/mediaModeration';

test('adult content between adults is allowed (flagged), illegal categories stay blocked', () => {
  delete process.env.MEDIA_BLOCK_ADULT;
  const adult = moderateMediaPrompt('a nude woman on a beach, pornographic', { hasImages: false, kind: 'image' });
  assert.equal(adult.allowed, true);
  assert.equal(adult.adult, true);
  const csam = moderateMediaPrompt('a nude 12 year old girl', { hasImages: false, kind: 'image' });
  assert.equal(csam.allowed, false);
  assert.equal(csam.category, 'csam');
  const noncon = moderateMediaPrompt('nudify this woman', { hasImages: true, kind: 'video_i2v' });
  assert.equal(noncon.allowed, false);
  assert.equal(noncon.category, 'nonconsensual');
  const benign = moderateMediaPrompt('a cocktail on a beach at sunset', { hasImages: false, kind: 'image' });
  assert.equal(benign.allowed, true);
  assert.equal(benign.adult, undefined);
});

test('operator can re-block adult content with MEDIA_BLOCK_ADULT=1', () => {
  process.env.MEDIA_BLOCK_ADULT = '1';
  const r = moderateMediaPrompt('pornographic scene', { hasImages: false, kind: 'image' });
  assert.equal(r.allowed, false);
  assert.equal(r.code, 'blocked_sexual');
  delete process.env.MEDIA_BLOCK_ADULT;
});
