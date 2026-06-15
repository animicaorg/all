import Link from "next/link";

export const metadata = {
  title: "ENA — open, community-trained AI · Animica Pool",
  description:
    "ENA is Animica's open AI layer: train one model together in pools, serve it while it trains, and use it as a coding agent — all paid in ANM.",
};

export default function AboutEnaPage() {
  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <h1 className="text-3xl font-semibold">
          ENA — open AI, <span className="text-neon-green">trained by everyone</span>
        </h1>
        <p className="max-w-3xl text-white/70">
          ENA turns Animica mining into useful AI work. One command runs a miner that
          earns ANM for proof-of-work <em>and</em> for AI: contributing data, training
          a shared model, serving it for inference, and (on qualified GPUs) Bittensor.
          Everything settles to your Animica address.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <Link href="/training-pools" className="btn-primary">Training pools →</Link>
          <Link href="/download" className="text-white/70 hover:text-white">Download a GUI miner</Link>
        </div>
        <pre className="mt-3 w-fit rounded-xl border border-white/10 bg-black/40 p-4 text-sm">
pip install --upgrade animica{"\n"}animica up</pre>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[
          ["Train together (pools)", "Many contributors fund and train ONE model in rounds. The dataset is sharded across trainers; verified work is rewarded proportionally."],
          ["Serve while training", "The promoted checkpoint is served over an OpenAI-compatible API while the next round trains. Servers earn per token, in ANM."],
          ["One global model", "Every pool — run by anyone — converges on a single canonical model: a shared on-chain head all servers serve and all clients use."],
          ["Useful-work mining", "CPU miners also do scrape / clean / embed / eval jobs; each job is receipted and paid. Mining funds the model."],
          ["Coding agent", "Use the pool model as a sandboxed coding agent (`animica ena code`) over any OpenAI-compatible endpoint."],
          ["ANM-only rewards", "PoW, useful-work, training, serving, and Bittensor all pay out in ANM to your address. No external payout to manage."],
        ].map(([h, b]) => (
          <div key={h} className="rounded-xl border border-white/10 bg-white/5 p-5">
            <h3 className="font-semibold">{h}</h3>
            <p className="mt-2 text-sm text-white/60">{b}</p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-white/10 bg-white/5 p-5">
        <h2 className="text-xl font-semibold">How it fits together</h2>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-white/70">
          <li>Run <code className="text-neon-green">animica up</code> — it auto-creates a wallet and mines + does AI by capability.</li>
          <li>Fund a <Link href="/training-pools" className="underline">training pool</Link> to accelerate the global model.</li>
          <li>GPU miners train shards and serve the promoted checkpoint; qualified GPUs also serve Bittensor.</li>
          <li>Build with the model via the OpenAI-compatible endpoint or the ENA coding agent.</li>
        </ol>
      </section>
    </div>
  );
}
