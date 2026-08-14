import type { Metadata } from 'next';
import { Code, K, Table, Callout, PageNav } from '../doc';
import { limits } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Agents & schedules — Animica Python Cloud',
  description: 'Persistent, capability-bounded programs with their own ANM identity, plus scheduled execution.',
};

const AGENT = `POST /api/cloud/v1/agents
{
  "slug": "price-watcher",
  "name": "Price Watcher",
  "functionId": "…",              // the deployed function this agent runs
  "capabilities": ["READ_CHAIN", "PERSIST_STATE"],
  "maxSpendPerRunNanm": "10000000",
  "dailySpendCapNanm": "100000000"
}

POST /api/cloud/v1/agents/{slug}/run     { "payload": { … } }`;

const NESTED = `import animica

def main(request, ctx):
    # delegate to another published function — a real nested execution
    res = animica.call("examples/anm-toolkit", {"op": "convert", "anm": "0.25"})
    return {"delegated": res["status"], "cost_nanm": res["cost_nanm"], "out": res["result"]}`;

const SCHEDULE = `POST /api/cloud/v1/schedules
{ "functionId": "…", "kind": "interval", "intervalMinutes": 60, "payload": {} }

# or 5-field UTC cron (minute hour dom month dow; Vixie dom/dow semantics):
{ "functionId": "…", "kind": "cron", "cron": "*/15 * * * *" }`;

export default function Page() {
  return (
    <>
      <h1>Agents &amp; schedules</h1>
      <p className="cd-lead">
        An agent is a deployed function given a persistent identity and an autonomy budget. Schedules make
        any function run on its own cadence. Together they are the platform&apos;s answer to &quot;programs that act
        on their own — inside limits their owner set&quot;.
      </p>

      <h2>Agents</h2>
      <Code title="create and run an agent">{AGENT}</Code>
      <ul>
        <li>
          An agent can carry its <b>own native anim1… address</b> — a real identity it can be paid at.
        </li>
        <li>
          <K>maxSpendPerRunNanm</K> and <K>dailySpendCapNanm</K> are enforced server-side by the host broker
          on every spend, atomically — an agent cannot exceed them no matter what its code does.
        </li>
        <li>
          Agent runs are ordinary executions (<K>callerKind: &quot;agent&quot;</K>) with the full admission stack:
          plan, quotas, affordability, settlement.
        </li>
        <li>
          Status: <K>ACTIVE</K> / <K>PAUSED</K> / <K>DISABLED</K> / <K>SUSPENDED</K> — anything but ACTIVE
          refuses runs.
        </li>
      </ul>

      <h2>Agent-to-agent calls</h2>
      <p>
        Inside any execution, <K>animica.call(&quot;owner/slug&quot;, payload)</K> invokes another published function
        as a nested execution in the same call tree:
      </p>
      <Code title="nested execution">{NESTED}</Code>
      <Table
        head={['guard', 'value', 'behavior when exceeded']}
        rows={[
          ['call depth', String(limits.maxCallDepth), <K key="1">depth_exceeded</K>],
          ['calls per execution', String(limits.maxCallsPerExecution), <K key="2">budget_exceeded</K>],
          ['shared spend budget', 'the root caller’s authorization (maxSpendNanm or the pre-execution estimate)', <K key="3">budget_exceeded — refused before the nested call runs</K>],
          ['self-calls', 'never allowed', <K key="4">recursion</K>],
        ]}
      />
      <p>
        Each hop returns <K>{'{status, result, request_id, cost_nanm}'}</K>, and every nested run is a real
        execution row with <K>parentExecutionId</K>/<K>rootId</K>/<K>depth</K> — the whole trace is
        reconstructable and billed transparently. The working{' '}
        <a href="/docs/cloud/examples">agent-calls-app example</a> shows a paid nested call end to end.
      </p>

      <h2>Schedules</h2>
      <Code title="create a schedule">{SCHEDULE}</Code>
      <ul>
        <li>
          The scheduler fires due schedules through the normal execution path — <K>callerKind:
          &quot;schedule&quot;</K>, the schedule&apos;s <b>owner</b> as the paying account, full admission control applied.
          Since the owner is usually the function&apos;s developer, scheduled runs of your own function consume
          plan quota rather than ANM.
        </li>
        <li>
          Minimum interval is your plan&apos;s <K>min_schedule_minutes</K> (Free: hourly · Developer: 15 min ·
          Pro/Business: 5 min), never below the platform floor of {limits.minScheduleMinutes} minutes.
        </li>
        <li>
          Soft failures (quota, funds, capacity) defer the run; hard failures count toward auto-disable
          after 5 consecutive failures, with the reason recorded on the schedule.
        </li>
        <li>
          Use <K>animica.state</K> for memory between runs — see the working{' '}
          <a href="/docs/cloud/examples">scheduled-agent example</a>.
        </li>
      </ul>

      <Callout>
        Schedules and agent budgets are enforced by the backend — the scheduler adds nothing money-shaped of
        its own, so there is no path where an agent &quot;runs for free&quot; or outspends its caps.
      </Callout>
      <PageNav current="/docs/cloud/agents" />
    </>
  );
}
