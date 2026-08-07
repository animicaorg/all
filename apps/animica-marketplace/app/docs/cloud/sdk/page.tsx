import type { Metadata } from 'next';
import { Code, K, Callout, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'Python SDK — Animica Python Cloud',
  description: 'The in-sandbox animica module, and calling / deploying functions from client-side Python.',
};

const INSIDE = `# INSIDE a deployed function: `+ '`import animica`' + ` is the SDK.
# It is built into the runtime — nothing to install, nothing to configure.
import animica

def main(request, ctx):
    animica.log("hello from the runtime")
    return {"balance": animica.chain.balance(ctx.owner)}`;

const CLIENT_CALL = `# OUTSIDE the sandbox (your laptop, a server): a function is just HTTPS.
import json, urllib.request

def call_function(owner, slug, payload, api_key=None):
    req = urllib.request.Request(
        f"https://animica.dev/api/cloud/v1/fn/{owner}/{slug}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json",
                 **({"authorization": f"Bearer {api_key}"} if api_key else {})},
    )
    with urllib.request.urlopen(req) as res:
        return {
            "result": json.load(res),
            "request_id": res.headers.get("x-animica-request-id"),
            "cost_nanm": int(res.headers.get("x-animica-cost-nanm", "0")),
        }

out = call_function("examples", "hello-api", {"name": "Ada"})
# requests/httpx work identically if you prefer them — this is plain HTTP + JSON`;

const CLIENT_DEPLOY = `# deploying from Python is two REST calls
import json, pathlib, urllib.request

BASE = "https://animica.dev/api/cloud/v1"
KEY = "anm_mkt_…"

def api(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)

fn = api("/functions", {"slug": "hello", "timeoutMs": 10000, "memoryMb": 128})
dep = api(f"/functions/{fn['function']['id']}/versions", {
    "source": pathlib.Path("handler.py").read_text(),
    "entrypoint": "main",
    "packages": [],
})
print(dep["deployment"]["status"], dep["deployment"].get("anchorTxid"))`;

export default function Page() {
  return (
    <>
      <h1>Python SDK</h1>
      <p className="cd-lead">
        There are two Python surfaces: the <K>animica</K> module <b>inside</b> the runtime (built in, zero
        install), and plain HTTPS <b>outside</b> it. No client library is required for either.
      </p>

      <h2>Inside the sandbox: <K>import animica</K></h2>
      <p>
        The runtime injects the <K>animica</K> module into every execution — it <em>is</em> the SDK. Full
        reference on the <a href="/docs/cloud/runtime">Runtime &amp; ABI</a> page:
      </p>
      <Code title="the runtime SDK">{INSIDE}</Code>
      <ul>
        <li>
          <K>animica.ai</K> · <K>animica.chain</K> · <K>animica.wallet</K> · <K>animica.state</K> ·{' '}
          <K>animica.http</K> · <K>animica.call()</K> · <K>animica.log()</K> · <K>animica.secret()</K>
        </li>
        <li>
          Typed errors: <K>animica.AnimicaError</K>, <K>animica.CapabilityDenied</K>,{' '}
          <K>animica.BudgetExceeded</K>
        </li>
      </ul>
      <Callout>
        Inside the sandbox there is deliberately <b>no</b> pip and no HTTP client — the host API is the
        only bridge to the world. That inversion is the security model, not a limitation of the SDK. See{' '}
        <a href="/docs/cloud/packages">Supported packages</a>.
      </Callout>

      <h2>Calling functions from Python</h2>
      <Code title="a deployed function is just an HTTPS endpoint">{CLIENT_CALL}</Code>

      <h2>Deploying from Python</h2>
      <Code title="deploy in two calls">{CLIENT_DEPLOY}</Code>
      <p>
        The deploy response carries the version number, the deployment status, the DA blob id and the
        on-chain anchor txid (or the honest reason there isn&apos;t one). Add an <K>idempotency-key</K> header
        to make retries safe. The full surface — estimates, logs, executions, earnings — is on the{' '}
        <a href="/docs/cloud/api">REST API</a> page.
      </p>
      <PageNav current="/docs/cloud/sdk" />
    </>
  );
}
