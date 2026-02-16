# SCaffold Audit

## Inventory of scaffolds found and fix status

- [x] `src/modal/modalClient.ts` returned deterministic scaffold output when `MODAL_CHAT_URL` missing.  
  **Fix:** replaced with timeout + retry Modal client and explicit structured error.
- [x] `src/server/simulate/simulateAdapter.ts` returned non-functional placeholder simulation text only.  
  **Fix:** implemented bytecode-based admission simulation result.
- [x] `app/app/ChatWorkspace.tsx` had non-functional action buttons, demo-only local state flows, and missing diagnostics/error UX.  
  **Fix:** wired chat/deploy/memory/settings/knowledge-pack flows + diagnostics drawer + copy actions + explain-error action.
- [x] `app/api/deploy/route.ts` queued synthetic tx tracking with no real receipt polling.  
  **Fix:** now builds deploy tx payload, signs, submits via RPC compatibility layer, polls receipt, and returns explorer link.
- [x] `src/server/wallet/signer.ts` returned "not implemented" for wallet signing.  
  **Fix:** added feature-flagged wallet path + working local-key signer path over built tx payload.
- [x] `app/api/wallet/mock-approve/route.ts` and modal UI relied on mock approval flow.  
  **Fix:** removed client usage and endpoint now returns deprecation error.
- [x] Strict/Possibility mode had no persisted user settings path.  
  **Fix:** added `app/api/settings` and durable user state storage.
- [x] Project memory persistence/versioning path was missing.  
  **Fix:** added server-side JSON-backed memory store with a 10-revision cap.
- [x] Knowledge pack build trigger/status path was missing.  
  **Fix:** added `app/api/knowledge-pack/build` status + trigger wiring.
- [x] RPC startup discovery + param compatibility test utility path was missing.  
  **Fix:** added startup discover boot + `Send Test Tx` API+UI flow.
- [x] Critical tests were missing for validator/rewrite and memory persistence.  
  **Fix:** added validator, project-memory, deploy-format, and expanded RPC tests.
