import { describe, expect, it } from 'vitest';
import { createExplorerStore } from '../../src/state/store';

describe('explorer store head rollback guard', () => {
  it('ignores suspicious deep head rollback', () => {
    const store = createExplorerStore();

    store.getState().setHead({ height: 200, hash: '0x200' });
    expect(store.getState().head.height).toBe(200);

    // Greater than MAX_ALLOWED_ROLLBACK (32) should be ignored.
    store.getState().setHead({ height: 1, hash: '0x1' });

    expect(store.getState().head.height).toBe(200);
    expect(store.getState().head.hash).toBe('0x200');
  });

  it('allows normal short rollback for reorg handling', () => {
    const store = createExplorerStore();

    store.getState().setHead({ height: 200, hash: '0x200' });
    store.getState().setHead({ height: 190, hash: '0x190' });

    expect(store.getState().head.height).toBe(190);
    expect(store.getState().head.hash).toBe('0x190');
  });
});
