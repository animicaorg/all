import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { limits, economics } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'AI — Animica Python Cloud',
  description: 'animica.ai.infer: miner-served AI inference inside your functions, metered per token.',
};

const INFER = `import animica

def main(request, ctx):
    # simple prompt form
    text = animica.ai.infer("Summarize: " + request["text"], max_tokens=200)

    # chat form (OpenAI-style messages)
    text = animica.ai.infer(
        messages=[
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": request["question"]},
        ],
        max_tokens=300,
        temperature=0.2,
    )
    return {"answer": text}`;

const DEGRADE = `try:
    summary = animica.ai.infer(prompt, max_tokens=220)
    engine = "animica-ai"
except animica.BudgetExceeded:
    # this execution's AI call/token budget is used up
    raise
except animica.AnimicaError:
    # no healthy provider served the request — degrade honestly
    summary = extractive_summary(text)     # real, deterministic, in-sandbox
    engine = "extractive-fallback"`;

export default function Page() {
  return (
    <>
      <h1>AI</h1>
      <p className="cd-lead">
        <K>animica.ai.infer</K> gives every function access to AI inference served by the Animica miner
        network. Calls are mediated by the host broker, metered per token, and billed into the same
        execution receipt as CPU and memory.
      </p>

      <h2>Using it</h2>
      <p>
        Declare the <K>AI_INFERENCE</K> capability on your function — without it, every call is refused
        with <K>CapabilityDenied</K>.
      </p>
      <Code title="both forms">{INFER}</Code>
      <ul>
        <li>
          <K>animica.ai.chat(messages, …)</K> is an alias of <K>infer(messages=…)</K>.
        </li>
        <li>
          The last 20 messages are forwarded; each message&apos;s content is capped at 20,000 characters.
        </li>
        <li>
          <K>model</K> is optional — requests route to what the miner network is serving (the free,
          chain-served tier).
        </li>
      </ul>

      <h2>Budgets</h2>
      <Table
        head={['budget', 'default', 'error when exceeded']}
        rows={[
          ['AI calls per execution', String(limits.maxAiCallsPerExecution), <K key="1">BudgetExceeded</K>],
          ['AI tokens per execution', String(limits.maxAiTokensPerExecution), <K key="2">BudgetExceeded</K>],
          ['max_tokens per call', `capped at ${limits.maxAiTokensPerExecution}`, 'clamped, not an error'],
          ['per-call wall clock', '60s', <K key="3">AnimicaError (ai_unavailable)</K>],
        ]}
      />

      <h2>What it costs</h2>
      <p>
        AI tokens are metered at <K>aiTokenInNanm</K> ({economics.aiTokenInNanm.toString()} nANM/token
        default) and <K>aiTokenOutNanm</K> ({economics.aiTokenOutNanm.toString()} nANM/token default) from
        the live pricing policy — see <a href="/docs/cloud/pricing">Pricing &amp; economics</a>. Token
        counts come from the serving miner&apos;s usage report and appear in the execution receipt
        (<K>usage.aiTokensIn</K>/<K>aiTokensOut</K>).
      </p>

      <h2>Design for degraded serving</h2>
      <p>
        Inference is served by a decentralized miner fleet: availability is real-world, not guaranteed.
        When no healthy provider serves a request, <K>ai.infer</K> raises <K>animica.AnimicaError</K> after
        its 60s window. Production functions should decide what happens next — fail the request, retry
        later via a <a href="/docs/cloud/agents">schedule</a>, or degrade to a deterministic computation
        and say so:
      </p>
      <Code title="honest degradation (from the working ai-summarizer example)">{DEGRADE}</Code>
      <Callout tone="warn">
        The 60-second wait of a failed AI attempt is billed CPU/memory time like any other execution time.
        If your function can answer without AI, catching <K>AnimicaError</K> and degrading keeps your
        endpoint useful — and your response should always tell the caller which engine produced the
        result, as both <a href="/docs/cloud/examples">AI examples</a> do.
      </Callout>

      <h2>Serving AI to the network</h2>
      <p>
        The miners answering these calls earn ANM for it. Any capable machine can join:{' '}
        <K>pip install -U animica &amp;&amp; animica up</K> qualifies the hardware and serves the models it
        can actually run. That supply side is what keeps in-function AI available — see{' '}
        <a href="/docs/cloud/providers">Compute providers</a> for the Python Cloud execution fleet, which
        works the same way.
      </p>
      <PageNav current="/docs/cloud/ai" />
    </>
  );
}
