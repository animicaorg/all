import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { limits, economics } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Compute providers — Animica Python Cloud',
  description: 'Run the execution fleet: register, claim jobs, submit results, earn spendable ANM.',
};

const REGISTER = `POST /api/cloud/v1/providers/register
{
  "address": "anim1…",            // your bech32m ANM payout address (required)
  "token": "<your bearer, >=32 chars>",   // or omit and the server mints one — shown ONCE
  "name": "rack-3",
  "capabilities": ["python3.12"],
  "cpu_cores": 8, "memory_mb": 16384, "gpu": null
}`;

const LOOP = `# the provider loop
while true:
  job = POST /providers/claim            # atomic claim + lease; 204 = nothing to do
  if job:
    run job.payload in the SAME hardened runtime image  # GET /providers/runtime
    POST /providers/heartbeat { job_id }  # extend the lease while running
    POST /providers/result    { job_id, status, result, stdout, logs, usage }
  sleep(poll_interval)`;

export default function Page() {
  return (
    <>
      <h1>Compute providers</h1>
      <p className="cd-lead">
        The execution fleet is open: any operator can register a machine, claim eligible executions, run
        them in the standard hardened runtime, and earn {economics.providerShareBps / 100}% of every
        execution they serve — as a real, immediately spendable ledger credit, never an IOU.
      </p>

      <h2>Registering</h2>
      <Code title="self-registration">{REGISTER}</Code>
      <ul>
        <li>
          Only the SHA3-256 of your bearer token is stored. The token authenticates every subsequent call.
        </li>
        <li>
          Registration is idempotent on the token — re-registering updates your capabilities and
          specifications.
        </li>
      </ul>

      <h2>The job loop</h2>
      <Code title="claim → run → report">{LOOP}</Code>
      <Table
        head={['mechanism', 'value', 'why']}
        rows={[
          ['claim', 'atomic SKIP-LOCKED', 'two providers can never grab the same job'],
          ['lease', `${limits.jobLeaseSeconds}s, extended by heartbeat`, 'a crashed provider’s job requeues instead of hanging'],
          ['attempts', `${limits.jobMaxAttempts}`, 'bounded retries; then the job expires and the execution fails honestly'],
          ['staleness', `${limits.providerStaleSeconds}s without heartbeat → IDLE`, 'dispatch skips silent machines'],
          ['priority', 'the CALLER’s plan priority class', 'paid tiers run first when the queue is contended'],
        ]}
      />

      <h2>What gets dispatched to the fleet</h2>
      <p>Dispatch is conservative — an execution goes to the fleet only when ALL of these hold:</p>
      <ul>
        <li>the compute market is enabled, and at least one provider is online;</li>
        <li>
          the function declares <b>no capabilities</b> (host-API calls need the gateway broker, which never
          leaves the platform);
        </li>
        <li>the function uses <b>no secrets</b> (secrets never leave the gateway);</li>
        <li>
          it is a <b>paid third-party call</b> (there is revenue to fund the provider share — own-function
          and free-tier runs stay local);
        </li>
        <li>the developer&apos;s in-flight fleet admission cap is not exceeded.</li>
      </ul>
      <p>Everything else runs on the platform&apos;s local sandbox lane with identical isolation.</p>

      <h2>Earnings</h2>
      <ul>
        <li>
          Your share is {economics.providerShareBps / 100}% of the customer&apos;s payment for each execution
          you served, settled in the same exactly-once transaction as the developer&apos;s share and the
          platform fee.
        </li>
        <li>
          It lands as a <K>SALE_CREDIT</K> on the ledger account linked to your payout address — spendable
          immediately, withdrawable like any balance. Provider compensation can never exceed the revenue
          allocated to compute (fee + provider share ≤ 100% is enforced).
        </li>
        <li>
          Results are verified structurally (size caps, status transitions) and reputation-tracked; failed
          or expired jobs count against reputation, and dispatch skips providers below the floor.
        </li>
      </ul>

      <Callout>
        Providers must run the standard runtime image (<K>GET /providers/runtime</K> tells you which) with
        the same hardening the platform applies — the job payload contains the artifact reference and the
        packed call, never plaintext secrets. See <a href="/docs/cloud/security">Security</a>.
      </Callout>
      <PageNav current="/docs/cloud/providers" />
    </>
  );
}
