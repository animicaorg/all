import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "About ENA — Open, community-trained AI · Animica",
  description:
    "ENA is open AI that the Animica network trains together. One global model, trained in pools, served while it trains, and funded by useful-work mining.",
};

function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-white/10 bg-white/5 p-6 ${className}`}
    >
      {children}
    </div>
  );
}

export default function AboutEnaPage() {
  return (
    <div className="space-y-12">
      {/* Hero */}
      <section className="space-y-4">
        <p className="text-sm font-medium uppercase tracking-wide text-blue-400">
          The Animica network AI
        </p>
        <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
          ENA is open AI, trained by everyone.
        </h1>
        <p className="max-w-2xl text-lg text-white/70">
          ENA (Emergent Network Agent) is a community-trained AI model that the
          Animica network builds together. Instead of one company training a
          model behind closed doors, anyone can contribute compute, data, and
          funding — and the result is a single, open, continuously improving
          model that everyone can use.
        </p>
        <div className="flex flex-wrap gap-3 pt-2">
          <Link
            href="/training-pools"
            className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500"
          >
            Fund a training pool →
          </Link>
          <Link
            href="/mining-onboard"
            className="rounded-lg border border-white/10 px-4 py-2 font-medium text-white/80 hover:border-blue-500/50 hover:text-white"
          >
            Point your rig at a pool →
          </Link>
        </div>
      </section>

      {/* The five ideas */}
      <section className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight">
          What makes ENA different
        </h2>
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <h3 className="text-lg font-medium text-blue-300">
              Open and community-owned
            </h3>
            <p className="mt-2 text-sm text-white/60">
              ENA is not a product locked inside one lab. The model, the training
              recipes, and the datasets are built in the open by the people who
              run the network. Anyone can contribute and anyone can use what gets
              produced.
            </p>
          </Card>
          <Card>
            <h3 className="text-lg font-medium text-blue-300">
              Train-together pools
            </h3>
            <p className="mt-2 text-sm text-white/60">
              Training happens in <span className="text-white/80">pools</span>.
              A pool fixes a base model and a method (for example fine-tuning a
              base model on a curated dataset), then splits the work into shards
              that many machines train in parallel, round after round. You join
              the pool, contribute a shard, and your work is folded back into the
              shared model.
            </p>
          </Card>
          <Card>
            <h3 className="text-lg font-medium text-blue-300">
              Serve while you train
            </h3>
            <p className="mt-2 text-sm text-white/60">
              A pool does not go dark while it trains. Each round publishes a
              served checkpoint, so the model is usable for inference the whole
              time it is improving. Compute that is not busy training a shard can
              serve requests — useful work in both directions.
            </p>
          </Card>
          <Card>
            <h3 className="text-lg font-medium text-blue-300">
              One global model
            </h3>
            <p className="mt-2 text-sm text-white/60">
              Pools are not islands. Their improvements converge into a single
              canonical model for the whole network. Every contribution — a
              shard trained on a home GPU, a dataset, a funded reward — pushes the
              one global ENA forward, rather than fragmenting effort across dozens
              of forks.
            </p>
          </Card>
          <Card className="md:col-span-2">
            <h3 className="text-lg font-medium text-blue-300">
              Funded by useful-work mining
            </h3>
            <p className="mt-2 text-sm text-white/60">
              Animica mining is{" "}
              <span className="text-white/80">useful work</span>: the same rigs
              that secure the chain point their compute at ENA training pools.
              Funders bankroll a pool with ANM rewards; the coordinator pays those
              rewards out to the contributors — the people who trained the shards,
              supplied the data, and served inference — according to each pool&apos;s
              reward split. The economics that already pay miners now also pay for
              an open AI model.
            </p>
          </Card>
        </div>
      </section>

      {/* How it works end-to-end */}
      <section className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight">
          How a pool works, end to end
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <div className="text-sm font-semibold text-blue-400">1 · Fund</div>
            <p className="mt-2 text-sm text-white/60">
              A funder picks a pool and locks in an ANM reward budget from their
              Animica wallet. That budget is what the pool pays out to everyone
              who does the work.
            </p>
          </Card>
          <Card>
            <div className="text-sm font-semibold text-blue-400">2 · Train</div>
            <p className="mt-2 text-sm text-white/60">
              Miners join the pool and claim shards. Each round, they train their
              shard against the current checkpoint and submit the result. The
              coordinator verifies and aggregates contributions into the next
              checkpoint.
            </p>
          </Card>
          <Card>
            <div className="text-sm font-semibold text-blue-400">
              3 · Serve &amp; reward
            </div>
            <p className="mt-2 text-sm text-white/60">
              The served checkpoint answers inference requests while the next
              round trains. As work lands, the pool&apos;s budget is paid out to
              contributors by reward split, and the improvements roll up into the
              one global ENA model.
            </p>
          </Card>
        </div>
      </section>

      {/* Roles */}
      <section className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight">
          Where you fit in
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <h3 className="text-lg font-medium">You have a rig</h3>
            <p className="mt-2 text-sm text-white/60">
              Point it at a pool and earn ANM for training and serving. One
              command joins the pool and starts contributing to the global model.
            </p>
            <Link
              href="/mining-onboard"
              className="mt-3 inline-block text-sm text-blue-400 hover:text-blue-300"
            >
              Get started mining →
            </Link>
          </Card>
          <Card>
            <h3 className="text-lg font-medium">You want a model trained</h3>
            <p className="mt-2 text-sm text-white/60">
              Fund a pool with ANM and watch the network train it for you. You
              get a live view of rounds, shards, contributions, and budget spent.
            </p>
            <Link
              href="/training-pools"
              className="mt-3 inline-block text-sm text-blue-400 hover:text-blue-300"
            >
              Fund a pool →
            </Link>
          </Card>
          <Card>
            <h3 className="text-lg font-medium">You just want to use ENA</h3>
            <p className="mt-2 text-sm text-white/60">
              The served checkpoints are OpenAI-compatible. Point any OpenAI SDK
              at this hub&apos;s <code className="text-white/80">/v1</code>{" "}
              endpoint and start calling the open model.
            </p>
          </Card>
        </div>
      </section>

      {/* Footer CTA */}
      <section className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-6">
        <h2 className="text-xl font-medium">
          ENA gets better every round — join in.
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-white/60">
          Whether you bring a GPU, a dataset, or a budget, you are training the
          same open model. This page is the hub: fund a pool, watch it train, or
          point your rig at it.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Link
            href="/training-pools"
            className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500"
          >
            Training Pools
          </Link>
          <Link
            href="/mining-onboard"
            className="rounded-lg border border-white/10 px-4 py-2 font-medium text-white/80 hover:border-blue-500/50 hover:text-white"
          >
            Mining onboarding
          </Link>
        </div>
      </section>
    </div>
  );
}
