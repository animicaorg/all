import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { CAPABILITIES, SENSITIVE_CAPABILITIES } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Capabilities & permissions — Animica Python Cloud',
  description: 'The capability system: what functions may do, and who authorizes it.',
};

const CAP_DESC: Record<string, [string, string]> = {
  AI_INFERENCE: ['animica.ai.infer / animica.ai.chat', 'AI inference via the miner network, metered per token'],
  CALL_FUNCTION: ['animica.call("owner/slug", …)', 'nested execution of another published function'],
  CALL_APP: ['animica.call into an app’s functions', 'like CALL_FUNCTION, scoped to app targets'],
  READ_CHAIN: ['animica.chain.head / balance', 'read-only Animica chain access'],
  SPEND_ANM: ['animica.wallet.pay / balance', 'pay ANM from the CALLER’s balance, inside their granted caps'],
  PERSIST_STATE: ['animica.state.get / set / delete', 'per-function encrypted key/value store'],
  SCHEDULE: ['CloudSchedule', 'the function is allowed to be driven by schedules'],
  HTTP_FETCH: ['animica.http.fetch', 'host-mediated outbound HTTPS with SSRF guards'],
};

const GRANT = `# a user authorizes an app once, with hard bounds; revocable any time
POST /api/cloud/v1/grants
{
  "subjectKind": "app", "subjectId": "…",
  "capabilities": ["SPEND_ANM"],
  "maxPerCallNanm": "1000000",
  "maxPerExecNanm": "5000000",
  "dailyCapNanm": "50000000",
  "allowedPayees": ["anim1…"]
}
DELETE /api/cloud/v1/grants?id=…   # takes effect on the very next host call`;

export default function Page() {
  const sensitive = new Set<string>(SENSITIVE_CAPABILITIES);
  return (
    <>
      <h1>Capabilities &amp; permissions</h1>
      <p className="cd-lead">
        A function can do nothing beyond pure computation unless its deployment <b>declares</b> the
        capability — and for anything sensitive, the <b>caller</b> must additionally grant it. Both checks
        happen server-side in the host broker, against server-held state, on every single call.
      </p>

      <h2>The {CAPABILITIES.length} capabilities</h2>
      <Table
        head={['capability', 'unlocks', 'purpose', 'needs caller grant']}
        rows={CAPABILITIES.map((c) => [
          <K key={c}>{c}</K>,
          <code key={`${c}-api`}>{CAP_DESC[c][0]}</code>,
          CAP_DESC[c][1],
          sensitive.has(c) ? 'yes' : 'no',
        ])}
      />

      <h2>Two layers of consent</h2>
      <ol>
        <li>
          <b>The developer declares</b> capabilities on the function at deploy time. The deploy-time
          validator infers which capabilities the source appears to use (advisory, to pre-fill the UI) —
          but enforcement never trusts the source: the host broker checks the <b>declared</b> list on every
          host call and refuses anything undeclared with <K>CapabilityDenied</K>.
        </li>
        <li>
          <b>The caller grants</b> the sensitive subset ({SENSITIVE_CAPABILITIES.join(', ')}) explicitly,
          per app/agent/function, with hard budget bounds. No grant, no spend — even if the capability is
          declared.
        </li>
      </ol>
      <Code title="grants API">{GRANT}</Code>

      <h2>How SPEND_ANM actually moves money</h2>
      <ul>
        <li>User code never touches a key. It asks the host to pay; the host verifies everything.</li>
        <li>
          Checks on every payment, atomically: grant exists, not revoked/expired, includes{' '}
          <K>SPEND_ANM</K>; amount within <K>maxPerCallNanm</K>; recipient in <K>allowedPayees</K> (when
          set); running total within <K>maxPerExecNanm</K>; UTC-day total within <K>dailyCapNanm</K>{' '}
          (conditional-update claim — concurrent executions cannot double-spend the last allowance); and
          the whole call tree&apos;s budget not exceeded.
        </li>
        <li>
          The transfer itself is two balanced ledger postings (payer debit, payee credit) in one
          transaction — nothing is minted, nothing is lost.
        </li>
      </ul>

      <Callout>
        <b>Why this is real security, not advisory:</b> the sandbox has no network, no credentials and no
        keys — every privileged operation must pass through the broker, and the broker authorizes against
        the deployment row, the grant row and the live budgets, never against anything the guest sent.
        Revocation is read fresh at every spend, so it takes effect immediately.
      </Callout>

      <h2>Failure codes your code will see</h2>
      <ul>
        <li>
          <K>animica.CapabilityDenied</K> — capability not declared, or the caller&apos;s grant is missing /
          revoked / doesn&apos;t cover it.
        </li>
        <li>
          <K>animica.BudgetExceeded</K> — a spend/AI/call budget, quota or per-day cap would be exceeded.
        </li>
        <li>
          <K>animica.AnimicaError</K> — any other host-side failure (upstream unavailable, bad request…).
        </li>
      </ul>
      <PageNav current="/docs/cloud/capabilities" />
    </>
  );
}
