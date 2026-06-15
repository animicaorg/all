import Link from "next/link";
import type { Metadata } from "next";
import { Section } from "@/components/ui/Section";

export const metadata: Metadata = {
  title: "Mine ANM in one command — animica up",
  description:
    "Start mining Animica in one command. `animica up` runs proof-of-work and AI useful-work together — training and serving a shared open model — and pays out in ANM to a wallet it creates for you. CLI or GUI.",
  alternates: { canonical: "/mine" },
};

const STEPS: { n: string; title: string; body: string }[] = [
  {
    n: "1",
    title: "Install the CLI",
    body: "Python 3.10+ in a virtual environment. One pip install pulls the miner, trainer, and server.",
  },
  {
    n: "2",
    title: "Bring everything up",
    body: "`animica up` auto-creates an Animica wallet, downloads the right binaries for your OS, and starts working.",
  },
  {
    n: "3",
    title: "Earn ANM",
    body: "PoW + AI useful-work, training, and serving all settle to your wallet. Watch shares and earnings on Stats.",
  },
];

const DOES: string[] = [
  "Proof-of-work mining (SHA3)",
  "AI useful-work jobs — scrape, clean, embed, eval",
  "Trains the shared open model in pools",
  "Serves the model over an OpenAI-compatible API",
  "ANM-only payouts — no second coin, no payout config",
  "Auto-creates and manages your wallet",
];

export default function MinePage() {
  return (
    <div className="space-y-14">
      <Section
        eyebrow="One command"
        title={
          <>
            Mine ANM with <span className="grad-text">animica up</span>
          </>
        }
        description="One process does it all: proof-of-work plus AI useful-work, training, and serving — paid in ANM to a wallet it creates for you. No mode to pick, no payout asset to set."
      >
        <div className="flex flex-wrap gap-3">
          <Link href="/download" className="btn-primary">Download the GUI miner</Link>
          <Link href="/training-pools" className="btn-secondary">Training pools</Link>
          <Link href="/stats" className="btn-ghost">Live stats</Link>
        </div>
      </Section>

      {/* The one-command setup. */}
      <Section
        eyebrow="CLI setup"
        title="Two lines to start"
        description="Install the animica CLI in a Python 3.10+ virtual environment, then bring everything up. It auto-downloads the right miner binaries for your OS (macOS / Linux / Windows)."
      >
        <div className="grid gap-4 md:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.n} className="card h-full">
              <span className="badge">Step {s.n}</span>
              <h3 className="mt-3 text-lg font-medium text-white">{s.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-white/65">
                {renderInlineCode(s.body)}
              </p>
            </div>
          ))}
        </div>

        <div className="card overflow-hidden">
          <div className="mb-3 flex items-center gap-2">
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="ml-2 text-xs text-white/40">terminal</span>
          </div>
          <pre className="overflow-x-auto rounded-xl bg-black/50 p-4 font-mono text-sm leading-relaxed text-neon-green">
{`# create & activate a virtual environment (recommended)
python3 -m venv ~/animica-venv
source ~/animica-venv/bin/activate        # Windows: animica-venv\\Scripts\\activate

# install, then bring everything up
pip install --upgrade animica
animica up`}
          </pre>
          <p className="mt-4 text-sm text-white/60">
            That&apos;s the whole setup. <code className="rounded-md bg-black/40 px-1.5 py-0.5 font-mono text-[0.85em] text-neon-green">animica up</code>{" "}
            creates a wallet for you and starts mining + AI useful-work at once. Your shares and balance
            then show under your address in{" "}
            <Link href="/stats" className="text-neon-green hover:underline">Stats</Link>.
          </p>
        </div>
      </Section>

      {/* What the one command does. */}
      <Section
        eyebrow="Under the hood"
        title="What one command does"
        description="No mining modes to choose between — the unified flow runs everything that earns ANM, scaling to whatever your machine can handle."
      >
        <div className="card">
          <ul className="grid gap-3 sm:grid-cols-2">
            {DOES.map((d) => (
              <li key={d} className="flex items-start gap-2.5 text-sm text-white/75">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-neon-green" />
                {d}
              </li>
            ))}
          </ul>
        </div>
      </Section>

      {/* GUI alternative. */}
      <Section
        eyebrow="Prefer one click?"
        title="Download a GUI miner"
        description="A desktop app (Qt) for Windows, macOS, and Linux that runs the exact same unified flow as the CLI — mining + AI useful-work, training, and serving, all paid in ANM. No terminal required."
      >
        <div className="card flex flex-col items-start gap-4 md:flex-row md:items-center md:justify-between">
          <p className="text-sm text-white/60">
            Same wallet auto-creation, same ANM-only payouts — just a window instead of a terminal.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/download" className="btn-primary">Get the desktop app</Link>
            <Link href="/training-pools" className="btn-ghost">Training pools</Link>
            <Link href="/stats" className="btn-ghost">Live stats</Link>
          </div>
        </div>
      </Section>
    </div>
  );
}

/** Highlight backtick-wrapped spans as inline code. */
function renderInlineCode(text: string) {
  return text.split(/(`[^`]+`)/g).map((part, i) =>
    part.startsWith("`") && part.endsWith("`") ? (
      <code
        key={i}
        className="rounded-md bg-black/40 px-1.5 py-0.5 font-mono text-[0.8em] text-neon-green"
      >
        {part.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}
