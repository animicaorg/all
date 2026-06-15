import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Point your rig at a pool — Mining · Animica",
  description:
    "Run the unified Animica miner with one command: it joins a training pool, trains and serves the open ENA model, and earns you ANM.",
};

export default function MiningOnboardPage() {
  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <p className="text-sm font-medium uppercase tracking-wide text-blue-400">
          Mining onboarding
        </p>
        <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
          Point your rig at a pool.
        </h1>
        <p className="max-w-2xl text-white/70">
          The unified Animica miner turns your machine into a contributor to the
          open{" "}
          <Link href="/about-ena" className="text-blue-400 hover:text-blue-300">
            ENA model
          </Link>
          . It joins a{" "}
          <Link
            href="/training-pools"
            className="text-blue-400 hover:text-blue-300"
          >
            training pool
          </Link>
          , trains shards, serves inference between rounds, and earns ANM from the
          pool&apos;s reward budget — all from one command.
        </p>
      </section>

      <section className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight">
          Three steps to start earning
        </h2>

        <div className="space-y-4">
          {/* Step 1 */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-6">
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-semibold text-blue-400">1</span>
              <h3 className="text-lg font-medium">Install the unified miner</h3>
            </div>
            <p className="mt-2 text-sm text-white/60">
              One installer brings the whole stack — chain mining plus ENA
              training and serving. No separate tools to wire together.
            </p>
            <pre className="mt-3 overflow-x-auto rounded-lg border border-white/10 bg-black/40 p-4 text-sm text-white/90">
              <code>{`curl -fsSL https://animica.org/install.sh | sh`}</code>
            </pre>
          </div>

          {/* Step 2 */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-6">
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-semibold text-blue-400">2</span>
              <h3 className="text-lg font-medium">
                Run one command — it joins the pool and the global model
              </h3>
            </div>
            <p className="mt-2 text-sm text-white/60">
              Give it your Animica payout address and the pool you want to join.
              The miner auto-discovers rounds and shards, trains, serves, and
              folds your work into the one global ENA model.
            </p>
            <pre className="mt-3 overflow-x-auto rounded-lg border border-white/10 bg-black/40 p-4 text-sm text-white/90">
              <code>{`animica mine --pool <POOL_ID> --address anim1...`}</code>
            </pre>
            <p className="mt-2 text-xs text-white/40">
              Grab a <span className="text-white/60">POOL_ID</span> from the{" "}
              <Link
                href="/training-pools"
                className="text-blue-400 hover:text-blue-300"
              >
                Training Pools
              </Link>{" "}
              page. Omit <code className="text-white/60">--pool</code> to let the
              miner pick the canonical global pool automatically.
            </p>
          </div>

          {/* Step 3 */}
          <div className="rounded-xl border border-white/10 bg-white/5 p-6">
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-semibold text-blue-400">3</span>
              <h3 className="text-lg font-medium">Earn ANM, watch it train</h3>
            </div>
            <p className="mt-2 text-sm text-white/60">
              Your rig now trains shards and serves inference for the pool. Rewards
              from the pool&apos;s budget are paid out to your address by the
              pool&apos;s reward split. Track rounds, shards, and budget on the{" "}
              <Link
                href="/training-pools"
                className="text-blue-400 hover:text-blue-300"
              >
                Training Pools
              </Link>{" "}
              page.
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-white/10 bg-white/5 p-6">
        <h2 className="text-xl font-medium">Want the full guide?</h2>
        <p className="mt-2 max-w-2xl text-sm text-white/60">
          Hardware recommendations, GPU drivers, running as a service, and tuning
          live in the complete mining guide on animica.org.
        </p>
        <a
          href="https://animica.org/mine"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500"
        >
          Full mining guide at animica.org/mine →
        </a>
      </section>
    </div>
  );
}
