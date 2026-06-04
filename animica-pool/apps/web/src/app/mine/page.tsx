import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Mine ANM, Monero (XMR), or both — CPU mining setup",
  description:
    "Start CPU mining in one command: pure ANM (SHA3), pure Monero (XMR, RandomX), or dual 50/50. Install the animica CLI, point it at pool.animica.org, get paid in ANM or XMR.",
  alternates: { canonical: "/mine" },
};

const MODES = [
  { href: "/mine/anm", title: "Pure ANM", desc: "Mine Animica with SHA3. Steady PPS or solo. Optional ANM payouts." },
  { href: "/mine/xmr", title: "Pure Monero", desc: "Mine XMR (RandomX) and get paid in Monero — or convert to ANM." },
  { href: "/mine/dual", title: "Dual 50/50", desc: "Mine ANM + XMR at once, 50/50 thread split. Best total yield." },
];

export default function MinePage() {
  return (
    <div className="space-y-8">
      <section className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Mining</h1>
        <p className="max-w-2xl text-white/60">
          Pick a mode, set your payout asset (optional ANM payouts for every mode), and connect your
          miner to <code>pool.animica.org</code>. Live hashrate, shares, balance, and estimated earnings.
        </p>
      </section>
      <div className="grid gap-4 md:grid-cols-3">
        {MODES.map((m) => (
          <Link key={m.href} href={m.href} className="card hover:border-neon-violet/60">
            <h2 className="text-lg font-medium text-neon-blue">{m.title}</h2>
            <p className="mt-2 text-sm text-white/60">{m.desc}</p>
            <span className="mt-3 inline-block text-sm text-neon-green">Set up →</span>
          </Link>
        ))}
      </div>

      <section className="card space-y-3">
        <h2 className="text-lg font-medium text-neon-blue">Easiest: the one-command CLI miner</h2>
        <p className="text-sm text-white/60">
          Install the <code>animica</code> CLI in a Python virtual environment (Python 3.10+), then run the
          miner. It auto-downloads the right miner binaries for your OS (macOS/Linux/Windows) — no manual setup.
        </p>
        <ol className="ml-5 list-decimal space-y-3 text-sm text-white/70">
          <li>
            Create and activate a virtual environment:
            <pre className="mt-1 overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-neon-green">{`python3 -m venv ~/animica-venv
source ~/animica-venv/bin/activate        # Windows: animica-venv\\Scripts\\activate`}</pre>
          </li>
          <li>
            Install the CLI:
            <pre className="mt-1 overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-neon-green">{`pip install --upgrade animica`}</pre>
          </li>
          <li>
            Start mining (replace the address with your <code>anim1…</code> payout address and pick threads):
            <pre className="mt-1 overflow-x-auto rounded-lg bg-black/40 p-3 text-xs text-neon-green">{`animica miner dual-mine <your-anim1-address> \\
  --pool-host pool.animica.org --threads 8 --worker myrig`}</pre>
          </li>
        </ol>
        <p className="text-xs text-white/40">
          Dual mode mines ANM (SHA3) + Monero (RandomX) 50/50. For a single coin add{" "}
          <code>--only animica</code> or <code>--only xmr</code>. Your shares and balance then show under your
          address in <Link href="/stats" className="text-neon-green hover:underline">Stats</Link>.
        </p>
      </section>
    </div>
  );
}
