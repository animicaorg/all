import type { Metadata } from 'next';
import { Code, K, Callout, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'Wallets & earnings — Animica Python Cloud',
  description: 'How ANM flows: deposits, per-execution settlement, developer earnings, receipts.',
};

const FLOW = `caller balance ──(price)──┬── platform fee (feeBps)  ─→ treasury
                          └── developer share        ─→ your balance   (same transaction)
                              (+ provider share when a fleet provider ran it)`;

const EARNINGS = `GET /api/cloud/v1/me/earnings?days=30
{
  "paidNanm": "…",        // settled and already spendable (SALE_CREDIT ledger postings)
  "pendingNanm": "…",     // admitted but not yet settled (quoted reservations, not income)
  "byFunction": [ { "functionId": "…", "developerNanm": "…", "executions": … } ],
  "byDay":      [ { "day": "2026-08-01", "developerNanm": "…" } ]
}`;

const RECEIPT = `// returned by the authenticated invoke API; header equivalents on the public endpoint
"receipt": {
  "requestId": "rq_…", "executionId": "…", "function": "examples/anm-toolkit",
  "version": 1, "status": "succeeded", "asset": "ANM",
  "grossNanm": "5255894", "platformFeeNanm": "1051178",
  "developerNanm": "4204716", "providerNanm": "0", "creditNanm": "0",
  "feeBps": 2000,
  "usage": { "durationMs": 1053, "cpuMs": 1053, "memoryMbMs": "134784",
             "aiTokensIn": 0, "aiTokensOut": 0 },
  "freeTier": false, "ledgerRef": "…"
}`;

export default function Page() {
  return (
    <>
      <h1>Wallets &amp; earnings</h1>
      <p className="cd-lead">
        Every account has a platform ANM balance backed by an append-only double-entry ledger. Callers
        spend from it; developers earn into it — in the same atomic settlement, at the moment of
        execution.
      </p>

      <h2>Funding a balance</h2>
      <ul>
        <li>
          Deposit ANM from your wallet to your account&apos;s deposit address (Developer Center →{' '}
          <a href="/dev">wallet</a>). Deposits are credited after on-chain finality verification — the
          platform balance is backed 1:1 by verified deposits.
        </li>
        <li>
          Promotional <b>credits</b> (e.g. the Founding Developer grant) are drawn down before your real
          balance. Credits fund executions but are not withdrawable, and credit-funded revenue still pays
          the developer in full — the platform absorbs the difference.
        </li>
      </ul>

      <h2>Per-execution settlement</h2>
      <Code title="one execution, one transaction">{FLOW}</Code>
      <ul>
        <li>
          <b>Exactly once:</b> settlement claims the execution row conditionally inside the same
          transaction that posts the ledger entries — a retry or two racing workers cannot double-charge.
        </li>
        <li>
          <b>All or nothing:</b> the debit, the credits, the fee and the execution&apos;s financial fields
          commit together, and the entries for one execution always sum to zero.
        </li>
        <li>
          <b>Exact sum:</b> <K>price == platformFee + developer + provider</K> is asserted before the
          transaction may commit — a pricing bug fails the execution instead of minting ANM.
        </li>
        <li>
          <b>Affordability, not escrow:</b> before running, the platform checks balance + credits −
          in-flight reservations against the worst-case estimate, and refuses with{' '}
          <K>402 insufficient_funds</K> rather than letting a run it cannot settle start.
        </li>
      </ul>

      <h2>Your earnings</h2>
      <p>
        The developer share lands as a <K>SALE_CREDIT</K> ledger posting — <b>immediately spendable</b> on
        your own usage, app purchases or <K>animica.wallet.pay</K> flows, and withdrawable through the
        marketplace payout flow.
      </p>
      <Code title="earnings API">{EARNINGS}</Code>

      <h2>Receipts</h2>
      <p>Every execution produces a receipt with the full customer-side money breakdown:</p>
      <Code title="the receipt (real values from a live example run)">{RECEIPT}</Code>
      <p>
        The same numbers persist on the execution row and in your{' '}
        <K>/api/cloud/v1/functions/&#123;id&#125;/executions</K> history. Internal platform cost accounting
        (COGS, margins) is deliberately never exposed.
      </p>

      <Callout>
        <b>Integrity guarantees behind all of this:</b> every account&apos;s cached balance must equal the sum
        of its ledger entries; a nightly reconciliation job re-verifies that invariant, the per-execution
        exact-sum invariant and the zero-sum-per-ref invariant, and raises a finance alert on any
        disagreement — it never &quot;fixes&quot; records silently.
      </Callout>
      <PageNav current="/docs/cloud/earnings" />
    </>
  );
}
