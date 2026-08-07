import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'REST API — Animica Python Cloud',
  description: 'Complete reference for the /api/cloud/v1 surface: execution, functions, apps, agents, providers.',
};

const AUTH = `# API key (create in the Developer Center; scoped, prefix anm_mkt_)
curl -H "authorization: Bearer anm_mkt_…" https://animica.dev/api/cloud/v1/me

# or a signed-in browser session cookie (the console uses this)`;

const ERR = `{ "error": { "code": "insufficient_funds", "message": "…", "details": { … } } }`;

const HEADERS = `x-animica-request-id: rq_…       # ties the response to the execution + receipt
x-animica-cost-nanm:  5255894    # exact charge for this call, integer nANM
x-animica-status:     succeeded  # succeeded | failed | timeout`;

function R(method: string, path: string, desc: React.ReactNode, auth = 'key/session') {
  return [<K key={path + method}>{method}</K>, <code key={path}>{path}</code>, desc, auth];
}

export default function Page() {
  return (
    <>
      <h1>REST API</h1>
      <p className="cd-lead">
        Everything the console does goes through this API — there are no private endpoints with extra
        powers. Base URL: <K>https://animica.dev/api/cloud/v1</K>. All money fields are integer nANM
        strings.
      </p>

      <h2>Authentication</h2>
      <Code title="two ways in">{AUTH}</Code>
      <p>
        Write operations accept an <K>idempotency-key</K> header — retrying with the same key returns the
        original result instead of repeating the action. Errors are uniform:
      </p>
      <Code title="error shape">{ERR}</Code>
      <p>
        Notable codes: <K>401 unauthorized</K> · <K>402 insufficient_funds / quota_exceeded / plan_limit /
        billing_past_due</K> · <K>403 capability_denied / suspended / code_denied</K> ·{' '}
        <K>409 conflict / not_active</K> · <K>422 validation_failed / unsupported_package</K> ·{' '}
        <K>429 rate_limited / concurrency_limit / free_tier_limit</K> · <K>503 busy /
        validator_unavailable / disabled</K>.
      </p>

      <h2>Execution</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('GET/POST', '/fn/{owner}/{slug}', <>the public endpoint of a deployed function — GET query params or POST JSON body become the function&apos;s <K>request</K>; CORS-open</>, 'optional*'),
          R('POST', '/functions/{id}/invoke', <>authenticated invoke by id: <K>{'{ payload?, maxSpendNanm? }'}</K> — the console&apos;s Run button; returns result + logs + full receipt</>),
          R('POST', '/estimate', <>pre-execution price estimate: typical + worst case, full per-line breakdown, at <b>your</b> real fee rate</>),
          R('POST', '/validate', 'the exact deploy-time static validator, for editors (findings with line numbers)'),
        ]}
      />
      <p>* anonymous callers reach public, auth-optional, zero-surcharge functions inside the free tier.</p>
      <Code title="cost headers on every /fn response">{HEADERS}</Code>

      <h2>Functions &amp; deployments</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('GET | POST', '/functions', 'list my functions · create a function shell'),
          R('GET | PATCH | DELETE', '/functions/{id}', 'detail · update config (slug immutable) · archive (history preserved)'),
          R('GET | POST', '/functions/{id}/versions', 'immutable version history · push new source as version max+1 and deploy it'),
          R('POST', '/functions/{id}/deploy', <><K>{'{version?}'}</K> redeploy an existing version · <K>{'{deploymentId?}'}</K> resume a stalled attempt</>),
          R('POST', '/functions/{id}/rollback', <><K>{'{version}'}</K> — a NEW deployment of an OLD version</>),
          R('GET', '/functions/{id}/deployments', 'deployment history with per-step logs and anchor status'),
          R('GET', '/functions/{id}/executions', 'execution history: status, usage, exact money'),
          R('GET', '/functions/{id}/logs', 'execution logs, secret-redacted, cursor-paged'),
        ]}
      />

      <h2>Apps &amp; marketplace</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('GET | POST', '/apps', 'public catalog (filters, real popularity) · create an app', 'public / key'),
          R('GET | PATCH', '/apps/{slug}', 'listing detail · update my app'),
          R('POST', '/apps/{slug}/publish', 'publish / unpublish'),
          R('POST', '/apps/{slug}/purchase', 'buy (one-time or subscription period) — exact split settlement'),
          R('POST', '/apps/{slug}/authorize', 'grant the app capabilities with spend bounds'),
          R('GET | POST', '/apps/{slug}/reviews', 'reviews (verified users only)'),
          R('POST', '/reports', 'report an app / function / developer', 'public / key'),
        ]}
      />

      <h2>Agents, schedules, secrets, grants</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('GET | POST', '/agents', 'list my agents · create (function binding, budgets, own address)'),
          R('GET | PATCH | DELETE', '/agents/{slug}', 'detail · pause/resume/update budgets · delete'),
          R('POST', '/agents/{slug}/run', 'run the agent now (budgets enforced server-side)'),
          R('GET | POST | PATCH | DELETE', '/schedules', 'CRUD scheduled invocations (interval or 5-field UTC cron)'),
          R('GET | POST | DELETE', '/secrets', 'names+hints only · create/rotate (values are write-only) · delete'),
          R('GET | POST | DELETE', '/grants', 'my authorizations · create/update · revoke (immediate)'),
        ]}
      />

      <h2>Account</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('GET', '/me', 'resolved plan + entitlements + LIVE usage against every limit + balance + credits'),
          R('GET', '/me/earnings', 'settled vs pending earnings, by function and by day'),
          R('GET', '/me/analytics', 'execution analytics for my functions'),
          R('GET | POST', '/developers/handle', 'claim / check my public handle'),
          R('GET', '/developers/{handle}', 'public developer profile', 'public'),
          R('POST', '/enterprise', 'enterprise / dedicated-capacity inquiry', 'public'),
        ]}
      />

      <h2>Compute providers (the fleet)</h2>
      <Table
        head={['method', 'path', 'purpose', 'auth']}
        rows={[
          R('POST', '/providers/register', 'self-register a provider (payout address, capabilities); bearer token minted/held by you', 'provider token'),
          R('POST', '/providers/claim', 'atomically claim the next job (lease-based; 204 when none)', 'provider token'),
          R('POST', '/providers/heartbeat', 'liveness + extend a job lease', 'provider token'),
          R('POST', '/providers/result', 'submit a job outcome — settles the provider share as spendable ANM', 'provider token'),
          R('POST', '/providers/fail', 'report a failed attempt (bounded retries)', 'provider token'),
          R('GET', '/providers/runtime', 'the runtime image + protocol the fleet must run', 'provider token'),
          R('GET', '/providers', 'public fleet stats', 'public'),
        ]}
      />

      <Callout>
        Admin endpoints exist under <K>/api/cloud/v1/admin/*</K> (pricing policy versions, finance
        rollups, reconciliation reports, denylist, suspensions…). They require platform-admin auth and are
        documented in the operator reference (<K>docs/python-cloud.md</K> in the repository), not here.
      </Callout>
      <PageNav current="/docs/cloud/api" />
    </>
  );
}
