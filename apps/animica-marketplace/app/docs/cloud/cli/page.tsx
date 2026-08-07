import type { Metadata } from 'next';
import { Code, K, Callout, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'CLI — Animica Python Cloud',
  description: 'Working with Python Cloud from the terminal: curl workflows plus the animica chain CLI.',
};

const DEPLOY_SH = `# deploy.sh — create once, then push versions. Plain REST; nothing to install but curl+jq.
BASE=https://animica.dev/api/cloud/v1
AUTH="authorization: Bearer $ANM_KEY"

# 1) create the function shell (idempotent with an idempotency-key)
FN_ID=$(curl -s "$BASE/functions" -H "$AUTH" -H 'content-type: application/json' \\
  -H "idempotency-key: create-hello-1" \\
  -d '{"slug":"hello","timeoutMs":10000,"memoryMb":128}' | jq -r '.function.id')

# 2) push source as the next immutable version and deploy it
jq -Rs '{source: ., entrypoint: "main"}' < handler.py | \\
  curl -s "$BASE/functions/$FN_ID/versions" -H "$AUTH" \\
       -H 'content-type: application/json' -d @- | \\
  jq '{version: .version.version, status: .deployment.status, anchor: .deployment.anchorTxid}'`;

const OPS_SH = `# day-2 operations, same pattern
curl -s "$BASE/functions/$FN_ID/executions?limit=20" -H "$AUTH" | jq       # history + money
curl -s "$BASE/functions/$FN_ID/logs?limit=100" -H "$AUTH" | jq            # redacted logs
curl -s "$BASE/functions/$FN_ID/rollback" -H "$AUTH" -X POST \\
     -H 'content-type: application/json' -d '{"version": 1}' | jq          # roll back
curl -s "$BASE/me/earnings?days=30" -H "$AUTH" | jq                        # earnings`;

const CHAIN_CLI = `# the pip-installed animica CLI handles the CHAIN side: wallets, balances, transfers
pip install -U animica

animica wallet create --label me
animica wallet list
animica account balance anim1…            # check any address
# fund your platform balance by sending ANM to your deposit address (Developer Center)`;

export default function Page() {
  return (
    <>
      <h1>CLI</h1>
      <p className="cd-lead">
        Python Cloud is operated over its REST API — every operation is one curl away, and the{' '}
        <K>idempotency-key</K> header makes scripted retries safe. The pip-installed <K>animica</K> CLI
        covers the chain side: wallets, balances and transfers.
      </p>

      <Callout>
        <b>Honest status:</b> a dedicated <K>animica cloud</K> subcommand does not ship yet — the REST
        workflows below are the supported terminal path today (they are exactly what the web console
        calls). The platform itself uses the <K>animica</K> CLI under the hood to sign and broadcast the
        on-chain anchor DEPLOY transactions.
      </Callout>

      <h2>Deploy from the terminal</h2>
      <Code title="deploy.sh">{DEPLOY_SH}</Code>

      <h2>Operate from the terminal</h2>
      <Code title="logs, history, rollback, earnings">{OPS_SH}</Code>

      <h2>Chain-side: the animica CLI</h2>
      <Code title="wallets and balances">{CHAIN_CLI}</Code>
      <p>
        Get an API key in the <a href="/dev">Developer Center</a>. Keys are scoped (<K>read</K>,{' '}
        <K>publish</K>, …) — mint one per automation with only the scopes it needs, and rotate it there
        too.
      </p>

      <h2>Verify a deployment yourself</h2>
      <p>
        Every anchored deployment can be independently audited from any Animica node: fetch the DA blob by
        its id (<K>da.get</K>), SHA3-256 the bytes (they must equal the blob id), read the bundle&apos;s{' '}
        <K>source</K> and re-hash it against the <K>sourceSha3</K> bound in the anchor transaction&apos;s
        manifest. No trust in the platform required — that is the point of anchoring.
      </p>
      <PageNav current="/docs/cloud/cli" />
    </>
  );
}
