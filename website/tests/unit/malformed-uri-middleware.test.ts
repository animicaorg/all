import { describe, expect, it } from 'vitest';

import { sanitizeMalformedUri } from '../../scripts/malformed-uri-middleware.mjs';

describe('sanitizeMalformedUri', () => {
  it('leaves valid URLs unchanged', () => {
    const url = '/docs/quickstart%20guide?ref=%2Fdocs#install';

    expect(sanitizeMalformedUri(url)).toBe(url);
  });

  it('escapes stray percent signs', () => {
    const sanitized = sanitizeMalformedUri('/100% ready?progress=50%');

    expect(sanitized).toBe('/100%25 ready?progress=50%25');
    expect(() => decodeURI(sanitized)).not.toThrow();
  });

  it('escapes invalid percent-encoded byte sequences', () => {
    const sanitized = sanitizeMalformedUri('/%E0%A4%A');

    expect(sanitized).toBe('/%25E0%25A4%25A');
    expect(() => decodeURI(sanitized)).not.toThrow();
  });
});
