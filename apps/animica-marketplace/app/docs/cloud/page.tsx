import type { Metadata } from 'next';
import { Code, K, Callout, PageNav } from './doc';
import { limits, economics } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Quickstart — Animica Python Cloud',
  description: 'Deploy your first Python function to Animica in 60 seconds and get paid when people use it.',
};

// Static config values only (env-derived, not per-request), so this page can be static.

const HELLO = `def main(request):
    name = "world"
    if isinstance(request, dict) and request.get("name"):
        name = str(request["name"])[:80]
    return {"greeting": f"Hello, {name}!", "echo": request}`;

const CREATE = `curl -s https://animica.dev/api/cloud/v1/functions \\
  -H "authorization: Bearer $ANM_KEY" \\
  -H 'content-type: application/json' \\
  -d '{"slug": "hello", "name": "Hello", "timeoutMs": 10000, "memoryMb": 128}'
# -> { "function": { "id": "…", "slug": "hello", "status": "DRAFT", … } }`;

const DEPLOY = `curl -s https://animica.dev/api/cloud/v1/functions/$FUNCTION_ID/versions \\
  -H "authorization: Bearer $ANM_KEY" \\
  -H 'content-type: application/json' \\
  -d "$(python3 - <<'PY'
import json
print(json.dumps({"source": open("handler.py").read(), "entrypoint": "main"}))
PY
)"
# -> validates, snapshots version 1, stores the DA blob, broadcasts the
#    on-chain anchor DEPLOY tx, and activates the endpoint`;

const INVOKE = `curl -s https://animica.dev/api/cloud/v1/fn/<you>/hello \\
  -H 'content-type: application/json' -d '{"name": "Ada"}'
# -> {"greeting": "Hello, Ada!", "echo": {"name": "Ada"}}
#    response headers: x-animica-request-id, x-animica-cost-nanm, x-animica-status`;

export default function Page() {
  return (
    <>
      <h1>Animica Python Cloud</h1>
      <p className="cd-lead">
        Write Python. Deploy to Animica. Get paid when people use it. A deployed function gets a public
        HTTPS endpoint, metered execution billed in ANM, and an exact revenue split — the platform takes{' '}
        {economics.platformFeeBps / 100}%, you keep the rest as an immediately spendable balance.
      </p>

      <Callout>
        <b>How it actually works:</b> deployments are <b>anchored on-chain</b> (source hash + artifact hash +
        DA blob id inside a signed DEPLOY tx) and <b>executed off-chain</b> in a hardened container. Animica
        consensus does not execute arbitrary Python — vm_py CALL transactions revert on mainnet by design
        (raw exec is fail-closed; enabling it would be node RCE). Anyone can fetch the DA blob, re-hash it,
        and verify exactly what code serves your endpoint.
      </Callout>

      <h2>1. Write a function</h2>
      <p>
        One file, one module-level entrypoint. <K>request</K> is the parsed JSON body (POST) or the query
        parameters (GET); the return value must be JSON-serializable.
      </p>
      <Code title="handler.py">{HELLO}</Code>

      <h2>2. Create it</h2>
      <p>
        You need an API key (create one in the <a href="/dev">Developer Center</a>; keys are prefixed{' '}
        <K>anm_mkt_</K>) or a signed-in browser session. Create the function shell:
      </p>
      <Code title="create the function">{CREATE}</Code>

      <h2>3. Deploy it</h2>
      <p>
        Pushing source creates <b>version 1</b> — an immutable snapshot — and drives the full pipeline:
        static validation (AST-only, never executes your code) → canonical artifact hashes → DA blob →
        on-chain anchor DEPLOY tx → <K>ACTIVE</K>.
      </p>
      <Code title="deploy version 1">{DEPLOY}</Code>

      <h2>4. Call it — and share the link</h2>
      <p>
        Every deployed function is served at{' '}
        <K>/api/cloud/v1/fn/&#123;owner&#125;/&#123;slug&#125;</K>. Public functions with no surcharge can be
        called by anyone inside the free tier ({economics.freeExecutionsPerDay}/day per caller); priced or
        private functions require a key. Every response carries the execution&apos;s request id and exact cost.
      </p>
      <Code title="invoke">{INVOKE}</Code>

      <h2>What you get per call</h2>
      <ul>
        <li>
          <b>Metered billing</b>: base fee + CPU-ms + memory + AI tokens + egress, integer nANM —{' '}
          <a href="/docs/cloud/pricing">Pricing &amp; economics</a>.
        </li>
        <li>
          <b>Your split, settled instantly</b>: the caller&apos;s payment is divided exactly
          (price = platform fee + your share) in one ledger transaction —{' '}
          <a href="/docs/cloud/earnings">Wallets &amp; earnings</a>.
        </li>
        <li>
          <b>A receipt</b> with request id, usage and the full money breakdown.
        </li>
      </ul>

      <h2>Limits that apply to every function</h2>
      <ul>
        <li>
          Source ≤ {Math.round(limits.maxSourceBytes / 1024)} KB · timeout {limits.minTimeoutMs / 1000}s–
          {limits.maxTimeoutMs / 1000}s (default {limits.defaultTimeoutMs / 1000}s) · memory {limits.minMemoryMb}–
          {limits.maxMemoryMb} MB (default {limits.defaultMemoryMb} MB)
        </li>
        <li>
          Request body ≤ {Math.round(limits.maxRequestBytes / 1024)} KB · response ≤{' '}
          {Math.round(limits.maxOutputBytes / 1024)} KB · {limits.maxLogLines} log lines/run
        </li>
        <li>
          Nested calls: depth ≤ {limits.maxCallDepth}, ≤ {limits.maxCallsPerExecution} calls/run · AI: ≤{' '}
          {limits.maxAiCallsPerExecution} calls and ≤ {limits.maxAiTokensPerExecution} tokens/run
        </li>
      </ul>
      <p>
        Next: the <a href="/docs/cloud/runtime">runtime ABI</a>, the{' '}
        <a href="/docs/cloud/capabilities">capability system</a>, and six{' '}
        <a href="/docs/cloud/examples">working examples</a> you can copy.
      </p>
      <PageNav current="/docs/cloud" />
    </>
  );
}
