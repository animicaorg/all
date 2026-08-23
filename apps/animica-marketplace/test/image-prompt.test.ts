import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { compileImagePrompt, snapImageSize, estimateClipTokens } from '../lib/imagePrompt';

const vectors = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', 'python', 'animica', 'media', 'prompt_vectors.json'), 'utf8'),
) as { cases: Array<{ name: string; input: string; negative?: string; expect: Record<string, any> }> };

for (const c of vectors.cases) {
  test(`prompt compiler: ${c.name}`, () => {
    const out = compileImagePrompt(c.input, { negative: c.negative });
    const e = c.expect;
    if (e.prompt_starts !== undefined) assert.ok(out.prompt.startsWith(e.prompt_starts), `prompt=${JSON.stringify(out.prompt)} should start with ${JSON.stringify(e.prompt_starts)}`);
    if (e.prompt_contains) for (const s of e.prompt_contains) assert.ok(out.prompt.includes(s), `prompt=${JSON.stringify(out.prompt)} missing ${JSON.stringify(s)}`);
    if (e.negative !== undefined) assert.equal(out.negative, e.negative);
    if (e.negative_contains) for (const s of e.negative_contains) assert.ok(out.negative.includes(s), `negative=${JSON.stringify(out.negative)} missing ${s}`);
    if (e.negative_not_contains) for (const s of e.negative_not_contains) assert.ok(!out.negative.includes(s), `negative=${JSON.stringify(out.negative)} must not contain ${s}`);
    if (e.counts) assert.deepEqual(out.spec.counts, e.counts);
    if (e.grid) assert.deepEqual(out.spec.grid, e.grid);
    if (e.colors) assert.deepEqual(out.spec.colors, e.colors);
    if (e.layout) assert.deepEqual(out.spec.layout, e.layout);
    if (e.text) assert.deepEqual(out.spec.text, e.text);
    if (e.negated) assert.deepEqual(out.spec.negated, e.negated);
    if (e.truncation_risk !== undefined) assert.equal(out.truncation_risk, e.truncation_risk, `est_tokens=${out.est_tokens}`);
    if (e.idempotent) {
      const again = compileImagePrompt(out.prompt, { negative: out.negative });
      assert.equal(again.prompt, out.prompt);
      assert.equal(again.negative, out.negative);
    }
  });
}

test('every compiled prompt is idempotent', () => {
  for (const c of vectors.cases) {
    const out = compileImagePrompt(c.input, { negative: c.negative });
    const again = compileImagePrompt(out.prompt, { negative: out.negative });
    assert.equal(again.prompt, out.prompt, c.name);
  }
});

test('size snapping keeps /64 and bounds', () => {
  assert.deepEqual(snapImageSize(512, 512), { width: 512, height: 512 });
  assert.deepEqual(snapImageSize(500, 700), { width: 512, height: 704 });
  assert.deepEqual(snapImageSize(64, 64), { width: 256, height: 256 });
  assert.deepEqual(snapImageSize(1280, 1280), { width: 1280, height: 1280 });
});

test('token estimate is monotone-ish and cheap', () => {
  assert.ok(estimateClipTokens('a cat') < estimateClipTokens('a photorealistic cat wearing a top hat, studio lighting'));
});

test('does not over-strip content words at the head', () => {
  assert.equal(compileImagePrompt('render of a sports car').prompt, 'render of a sports car');
  assert.equal(compileImagePrompt('picture frame on a wall').prompt, 'picture frame on a wall');
  assert.equal(compileImagePrompt('a man who is wearing glasses').prompt, 'a man who is wearing glasses');
});
