-- Additive migration: finality-window fields for reorg-safe deposit crediting.
--
-- Balance-delta deposits (topping up the custodial marketplace ledger by sending ANM to a
-- per-account deposit address) were being credited immediately on the observed confirmed-balance
-- delta, with NO confirmation gate. These two columns let scanDepositAddress() age a newly
-- observed uncredited delta across TX_FINALITY_CONFIRMATIONS blocks (default 12) before crediting
-- it — the deposit-side analogue of the store payment watcher's 12-conf finality gate. A reorg
-- that removes the deposit block can no longer leave us having credited ledger money that is not
-- backed by on-chain funds.
--
-- ADDITIVE-ONLY. Both columns are NOT NULL with a DEFAULT, so existing rows backfill to 0 (fresh
-- window, nothing pending) with no rewrite risk. NOT applied by the app — the orchestrator applies
-- this and regenerates the Prisma client BEFORE the matching lib/deposit.ts code is deployed.

ALTER TABLE "DepositAddress" ADD COLUMN "pendingNanm" BIGINT NOT NULL DEFAULT 0;
ALTER TABLE "DepositAddress" ADD COLUMN "pendingSinceHeight" INTEGER NOT NULL DEFAULT 0;
