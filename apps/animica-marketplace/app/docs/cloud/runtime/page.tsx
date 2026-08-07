import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { limits } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Runtime & ABI — Animica Python Cloud',
  description: 'The exact contract between your Python code and the Animica Python Cloud runtime.',
};

const ABI = `# Either shape works — the runner inspects the signature:

def main(request):
    ...

def main(request, ctx):
    ...`;

const HOSTAPI = `import animica

# AI (AI_INFERENCE) — metered per token
text = animica.ai.infer("prompt", max_tokens=200)
text = animica.ai.infer(messages=[{"role": "user", "content": "hi"}],
                        model=None, max_tokens=None, temperature=None)
text = animica.ai.chat(messages)              # alias of infer(messages=...)

# Chain reads (READ_CHAIN)
head = animica.chain.head()                   # {"height": int, "hash": "0x…"}
nanm = animica.chain.balance("anim1…")        # int nANM

# Spending (SPEND_ANM — requires the CALLER's explicit grant)
animica.wallet.pay("anim1…", amount_nanm, memo="")
nanm = animica.wallet.balance()               # the caller's platform balance

# Per-function persistent state (PERSIST_STATE) — encrypted at rest
animica.state.set("key", {"any": "json"})     # <= 16 KB per value, <= 200 keys
value = animica.state.get("key", default=None)
animica.state.delete("key")

# Mediated outbound HTTP (HTTP_FETCH) — the sandbox itself has NO network
res = animica.http.fetch("https://…", method="GET", headers={}, body=None, timeout=10)
# -> {"status": int, "headers": {…}, "body": str, "truncated": bool}

# Call another deployed function (CALL_FUNCTION / CALL_APP)
res = animica.call("owner/slug", {"payload": 1})
# -> {"status": "succeeded"|…, "result": …, "request_id": "rq_…", "cost_nanm": "…"}

# Structured logging (always available)
animica.log("message", level="info")          # debug | info | warn | error

# Secrets injected into THIS execution (configured via the secrets API)
token = animica.secret("MY_API_TOKEN", default=None)`;

const CTX = `def main(request, ctx):
    ctx.request_id   # "rq_…" — matches the receipt and execution history
    ctx.function     # the function slug
    ctx.version      # deployed version number
    ctx.owner        # developer's anim1… address
    ctx.caller       # "account" or "anonymous"
    ctx.deadline_ms  # the configured timeout
    # plus the same helpers as the module: ctx.ai, ctx.chain, ctx.wallet,
    # ctx.state, ctx.http, ctx.call(), ctx.log(), ctx.secret()`;

const ERRORS = `import animica

try:
    animica.wallet.pay(to, amount)
except animica.CapabilityDenied:   # not declared / not granted by the caller
    ...
except animica.BudgetExceeded:     # spend/AI/call budget or quota exhausted
    ...
except animica.AnimicaError:       # any other host-side failure (e.g. AI unavailable)
    ...`;

export default function Page() {
  return (
    <>
      <h1>Runtime &amp; ABI</h1>
      <p className="cd-lead">
        The runtime contract is deliberately tiny: one Python module, one entrypoint, JSON in, JSON out,
        and a capability-mediated <K>animica</K> host API for everything beyond pure computation.
      </p>

      <h2>The entrypoint</h2>
      <p>
        Your source is deployed as a single module (<K>handler.py</K>) with a module-level entrypoint —{' '}
        <K>main</K> by default, configurable to any function name. It takes the request, and optionally a
        context object:
      </p>
      <Code title="the whole ABI">{ABI}</Code>
      <ul>
        <li>
          <K>request</K> — the parsed JSON body of a POST (≤ {Math.round(limits.maxRequestBytes / 1024)} KB),
          or the query-string parameters of a GET, as a dict.
        </li>
        <li>
          The return value must be <b>JSON-serializable</b>; it becomes the HTTP response body. A
          non-serializable return raises a clear <K>TypeError</K> inside your function.
        </li>
        <li>
          <K>print()</K> works normally and is captured into the execution&apos;s logs (it can never corrupt the
          runtime protocol — stdout is redirected before your code is imported).
        </li>
        <li>
          Uncaught exceptions produce a <K>function_error</K> response with your traceback in the execution
          logs; a run past its time budget produces <K>function_timeout</K>.
        </li>
        <li>
          <K>async def</K> entrypoints are not supported by the current ABI (the validator rejects them at
          deploy time).
        </li>
      </ul>

      <h2>The host API</h2>
      <p>
        <K>import animica</K> inside the sandbox gives you the host API. Every call below is a mediated RPC
        to the host broker, which checks the function&apos;s declared{' '}
        <a href="/docs/cloud/capabilities">capabilities</a>, the caller&apos;s grants and the remaining budgets{' '}
        <b>server-side</b> before performing the operation — the sandbox holds no credentials, keys or
        network access of its own.
      </p>
      <Code title="the animica module">{HOSTAPI}</Code>

      <h2>The context object</h2>
      <Code title="ctx">{CTX}</Code>

      <h2>Errors</h2>
      <p>Host-API failures are typed so your function can react precisely:</p>
      <Code title="error taxonomy">{ERRORS}</Code>

      <h2>Execution envelope</h2>
      <Table
        head={['limit', 'value', 'enforced by']}
        rows={[
          [<K key="1">timeout</K>, `${limits.minTimeoutMs / 1000}s – ${limits.maxTimeoutMs / 1000}s (default ${limits.defaultTimeoutMs / 1000}s)`, 'SIGALRM in-runner + host-side container SIGKILL'],
          [<K key="2">memory</K>, `${limits.minMemoryMb} – ${limits.maxMemoryMb} MB (default ${limits.defaultMemoryMb} MB)`, 'cgroup memory cap (+ equal swap cap) + rlimits'],
          [<K key="3">processes</K>, `${limits.maxPids} pids`, 'cgroup pids controller'],
          [<K key="4">/tmp</K>, `${limits.maxTmpfsMb} MB tmpfs (noexec, nosuid)`, 'container mount — the only writable path'],
          [<K key="5">response size</K>, `${Math.round(limits.maxOutputBytes / 1024)} KB`, 'host'],
          [<K key="6">logs</K>, `${limits.maxLogLines} lines × ${limits.maxLogLineChars} chars`, 'host'],
          [<K key="7">open files</K>, '128 (rlimit), 256 (container)', 'rlimit + ulimit'],
          [<K key="8">file writes</K>, '8 MB per file (RLIMIT_FSIZE)', 'rlimit'],
        ]}
      />
      <Callout>
        <b>Billing note:</b> the billed CPU time is the <b>host-measured container wall time</b> — the
        resource the platform actually reserves for you. Guest-reported CPU numbers are recorded for
        observability but never drive billing (hostile code could understate them).
      </Callout>

      <h2>Determinism &amp; environment</h2>
      <ul>
        <li>Python 3.12 (<K>python:3.12-slim</K>), run with <K>-I</K> (isolated) and <K>PYTHONHASHSEED=0</K>.</li>
        <li>
          Read-only filesystem; your code is bind-mounted read-only at <K>/app/code</K>. Only <K>/tmp</K> is
          writable, and it is wiped after every execution — use <K>animica.state</K> for persistence.
        </li>
        <li>
          No network interface exists inside the sandbox. Outbound HTTP is exclusively{' '}
          <K>animica.http.fetch</K>; <K>pip install</K> at runtime is impossible by construction — see{' '}
          <a href="/docs/cloud/packages">supported packages</a>.
        </li>
        <li>
          Each execution is a fresh container: no state leaks between runs, and module-level globals reset
          every time.
        </li>
      </ul>
      <PageNav current="/docs/cloud/runtime" />
    </>
  );
}
