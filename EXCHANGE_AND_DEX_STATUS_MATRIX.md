# Exchange And DEX Status Matrix

Date: 2026-04-07

| Surface | Path | Status | Evidence | Next gate |
| --- | --- | --- | --- | --- |
| Exchange admin web | `apps/admin-web/` | Blocked | `npm --prefix apps/admin-web run type-check` fails in `src/contexts/AuthContext.tsx` | Fix types and rebuild |
| CEX services | `cex/services/` | Unvalidated | Service tree exists, no runtime smoke in this pass | Add service startup and API smoke |
| CEX e2e harness | `cex/tests/e2e/` | Blocked | `npm --prefix cex/tests/e2e run build` fails with `tsc: not found` | Make harness buildable in workspace |
| Token and contract surface | `contracts/` | Unvalidated | Contract surface exists, no build or deployment smoke in this pass | Map token launch and pairing flow |
| SDK exchange dependencies | `sdk/` | Unvalidated | SDK tree exists, no exchange-flow validation in this pass | Prove client-side integration path |
| DEX and token launch web surface | `web/`, `website/`, `cex/apps/` | Unvalidated | No end-to-end token listing or ANM pairing smoke in this pass | Identify active surface and wire it end to end |

## Current Assessment

- The repo contains exchange-related structure, but RC evidence is not yet present.
- The admin surface is already red at type-check level.
- The CEX harness is not build-ready in the current workspace state.
- No credible DEX or token launch release claim can be made until the active surface is identified and exercised end to end.
