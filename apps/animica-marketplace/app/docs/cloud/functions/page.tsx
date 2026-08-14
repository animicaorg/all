import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { limits } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Functions — Animica Python Cloud',
  description: 'Deployments, immutable versions, rollbacks, on-chain anchoring and the public endpoint.',
};

const LIFECYCLE = `DRAFT → VALIDATING → BUILDING → AWAITING_SIGNATURE → BROADCASTING → CONFIRMING → ACTIVE
                                                                                  ↘ FAILED`;

const VERSIONS = `# push new source  -> immutable version max+1, deployed
POST /api/cloud/v1/functions/{id}/versions   { "source": "…", "entrypoint": "main", "packages": [] }

# redeploy an existing version (no new snapshot), or resume a stalled attempt
POST /api/cloud/v1/functions/{id}/deploy     { "version": 3 } | { "deploymentId": "…" }

# roll back = a NEW deployment pointing at an OLD version (history is never rewritten)
POST /api/cloud/v1/functions/{id}/rollback   { "version": 2 }`;

const INVOKE_ERR = `// failed executions return the developer's real error, plus what the run cost:
{
  "error": { "code": "function_error", "message": "…", "type": "ValueError" },
  "requestId": "rq_…",
  "cost": { "nanm": "2934181", "asset": "ANM" }
}`;

export default function Page() {
  return (
    <>
      <h1>Functions</h1>
      <p className="cd-lead">
        A function is the unit of deployment, execution and earnings: one owner, one slug, an immutable
        version history, and a public endpoint at{' '}
        <K>/api/cloud/v1/fn/&#123;owner&#125;/&#123;slug&#125;</K>.
      </p>

      <h2>Configuration</h2>
      <Table
        head={['field', 'meaning']}
        rows={[
          [<K key="s">slug</K>, 'URL identity, unique per owner, immutable after creation'],
          [<K key="e">entrypoint</K>, <>module-level callable name (default <K>main</K>)</>],
          [<K key="t">timeoutMs / memoryMb</K>, `execution envelope, clamped server-side to the platform ceilings (${limits.maxTimeoutMs / 1000}s / ${limits.maxMemoryMb} MB)`],
          [<K key="c">capabilities</K>, <>what the function may ask the host to do — see <a key="l" href="/docs/cloud/capabilities">capabilities</a></>],
          [<K key="p">perCallNanm</K>, 'your per-call surcharge in integer nANM, added on top of metered cost on successful runs'],
          [<K key="v">visibility</K>, <><K>PUBLIC</K> (marketplace-listed; needs the publishing entitlement) · <K>UNLISTED</K> (reachable by URL) · <K>PRIVATE</K> (owner only)</>],
          [<K key="a">requiresAuth</K>, 'force API-key auth even for a free public function'],
        ]}
      />

      <h2>Deployment lifecycle</h2>
      <p>Every deployment is tracked through a persisted lifecycle, with a full log at each step:</p>
      <Code title="CloudDeployment.status">{LIFECYCLE}</Code>
      <ol>
        <li>
          <b>VALIDATING</b> — AST-only static validation (never executes your code): syntax, entrypoint
          signature, import policy, dangerous-call patterns, secret-shaped literals (warning). Fails closed:
          a broken validator is a retryable 503, never a silent pass.
        </li>
        <li>
          <b>BUILDING</b> — the canonical artifact manifest is computed (<K>sourceSha3</K> = SHA3-256 of the
          verbatim source; <K>artifactSha3</K> = SHA3-256 of a sorted-key manifest binding source hash,
          entrypoint, package set and runtime) and the full bundle (manifest + verbatim source) is stored in
          the Animica DA layer, content-addressed — the blob id <em>is</em> the SHA3-256 of the bytes.
        </li>
        <li>
          <b>AWAITING_SIGNATURE → BROADCASTING</b> — the platform&apos;s anchor wallet signs an on-chain{' '}
          <b>DEPLOY (t=1) transaction</b> whose manifest binds{' '}
          <K>{'{owner, function, version, sourceSha3, artifactSha3, daBlobId}'}</K>.
        </li>
        <li>
          <b>CONFIRMING</b> — bounded wait for inclusion; the recorded confirmation depth keeps updating in
          the background until finality (12 confirmations).
        </li>
        <li>
          <b>ACTIVE</b> — the endpoint serves the new version.
        </li>
      </ol>
      <Callout>
        <b>Anchoring honesty:</b> deployments are anchored on-chain and executed off-chain in a hardened
        container — Animica consensus does not execute arbitrary Python (vm_py CALL txs revert on mainnet by
        design). If an anchor cannot be broadcast (say, the anchor wallet cannot pay gas), the deployment
        still activates and is <b>recorded as unanchored with the real reason</b> — the platform never
        fabricates a txid. Deploying costs the developer 0 nANM; the platform pays the anchor gas.
      </Callout>

      <h2>Versions, redeploys, rollbacks</h2>
      <p>
        Version history is <b>append-only</b>. A version row snapshots the verbatim source, its hashes, the
        validation report and the deploy-time cost estimate; it is never modified afterwards. A redeploy
        creates version <K>max+1</K>; a rollback creates a <b>new deployment</b> pointing at an old version
        row.
      </p>
      <Code title="version operations">{VERSIONS}</Code>

      <h2>The public endpoint</h2>
      <ul>
        <li>
          <K>GET</K> (query params → request) and <K>POST</K> (JSON body → request), CORS-open for public
          functions.
        </li>
        <li>
          <K>&#123;owner&#125;</K> is your handle, or your <K>anim1…</K> address before a handle is claimed.
        </li>
        <li>
          Response headers on every call: <K>x-animica-request-id</K>, <K>x-animica-cost-nanm</K>,{' '}
          <K>x-animica-status</K>.
        </li>
        <li>
          Anonymous callers reach only public, auth-optional, zero-surcharge functions, inside the{' '}
          <a href="/docs/cloud/pricing">free tier</a>; everything else needs an API key.
        </li>
        <li>
          Failures are billed events (metered resources only, no surcharge) and return the real error:
        </li>
      </ul>
      <Code title="error response (HTTP 500 / 504)">{INVOKE_ERR}</Code>

      <h2>Operational endpoints</h2>
      <p>
        Owner-side management lives under <K>/api/cloud/v1/functions</K>: list/create, detail/update/archive,
        version history, deployment history, per-execution history with exact money, and secret-redacted
        logs. See the <a href="/docs/cloud/api">REST API reference</a>.
      </p>

      <h2>Secrets</h2>
      <p>
        Store per-function or account-wide secrets via <K>POST /api/cloud/v1/secrets</K>. Values are sealed
        with AES-256-GCM before they touch the database, injected into executions as{' '}
        <K>animica.secret(&quot;NAME&quot;)</K>, redacted from stored logs, and never returned by any API after
        creation.
      </p>
      <PageNav current="/docs/cloud/functions" />
    </>
  );
}
