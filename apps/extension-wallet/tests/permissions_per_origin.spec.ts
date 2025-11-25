import { describe, it } from 'vitest'

// Vertical 8: Origin-scoped permissions and persistence behavior
// TODO: Flesh out with integration harness targeting background + content script boundary.

describe('permissions per origin', () => {
  it.todo('tracks connections on a per-origin basis and prompts for approval')
  it.todo('revokes permissions per origin without affecting other tabs/domains')
  it.todo('persists permissions across reloads and restores them on startup')
  it.todo('surfaces a UI affordance for users to inspect and clear origin permissions')
  it.todo('ensures content scripts do not leak permissions to unrelated origins')
})
