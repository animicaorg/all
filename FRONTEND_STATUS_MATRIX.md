# Frontend Status Matrix

Date: 2026-04-07

| Surface | Path | Status | Evidence |
| --- | --- | --- | --- |
| Explorer Web | `explorer-web/` | Partially green | `npm --prefix explorer-web test -- test/unit/sync.test.ts` -> `11 passed` |
| Explorer 2 | `explorer2/` | Unvalidated in this pass | Code present, no direct build or test run in this iteration |
| Studio Web | `studio-web/` | Red | `npm --prefix studio-web test -- test/unit/provider.test.ts` -> `2 failed` because provider helpers return promises where tests expect direct values |
| Website | `website/` | Unvalidated | No build or smoke run in this pass |
| Legacy Web Assets | `web/` | Unvalidated | Asset tree present, no live route or build validation in this pass |
| Admin Web | `apps/admin-web/` | Red | `npm --prefix apps/admin-web run type-check` fails in `src/contexts/AuthContext.tsx` |
| Chat Animica | `apps/chat-animica/` | Unvalidated | Package exists, no smoke run in this pass |
| Wallet Extension App | `apps/wallet-extension/` | Known drift, not stabilized | Focused RPC-client test output is red; dedicated stabilization loop still open |
| Root Wallet Extension | `wallet-extension/` | Unvalidated | Local `node_modules` absent in this pass; no stable smoke run captured |
| Miner Dashboard | `apps/miner-dashboard/` | Unvalidated | Directory exists, no smoke run in this pass |
| Miner GUI | `apps/miner-gui/` | Unvalidated | Directory exists, no smoke run in this pass |

## Current Frontend Truth

- At least one explorer unit surface is healthy.
- Studio provider detection is out of contract.
- Admin web does not type-check.
- Frontend RC status is not credible until explorer, studio, wallet extension, and admin web all pass focused smokes.
