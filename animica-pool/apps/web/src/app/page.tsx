import Link from "next/link";
import type { Metadata } from "next";
import { Hero } from "@/components/ui/Hero";
import { Section } from "@/components/ui/Section";

export const metadata: Metadata = {
  title: "Mine + AI in one command — Animica Pool",
  description:
    "Run one command to mine ANM and do useful AI work at the same time: proof-of-work, training a shared open model in pools, and serving it over an OpenAI-compatible API. ANM-only payouts, auto wallet, CLI or GUI.",
  alternates: { canonical: "/" },
};

const PILLARS: { title: string; body: string }[] = [
  {
    title: "One command, mine + AI",
    body: "`animica up` runs proof-of-work and AI useful-work together — train, serve, and earn from a single process. ANM-only payouts, wallet auto-created.",
  },
  {
    title: "Train together in pools",
    body: "Many contributors fund and train ONE model in rounds. The dataset is sharded across trainers; verified work is rewarded proportionally in ANM.",
  },
  {
    title: "Serve while you train",
    body: "The promoted checkpoint is served for inference while the next round trains. Servers earn per token — no idle GPUs, no wasted cycles.",
  },
  {
    title: "One global model",
    body: "Every pool, run by anyone, converges on a single canonical model: a shared on-chain head all servers serve and all clients use.",
  },
  {
    title: "OpenAI-compatible API",
    body: "Use the community model as a drop-in chat/completions endpoint. Point any OpenAI client at it, or run the built-in coding agent.",
  },
  {
    title: "GUI miners",
    body: "Prefer one click? Download the desktop app for Windows, macOS, or Linux — same unified flow as the CLI, no terminal required.",
  },
];

export default function Home() {
  return (
    <div className="space-y-16">
      <Hero
        eyebrow="One useful-work economy"
        title={
          <>
            Mine <span className="grad-text">+ AI</span> in one command
          </>
        }
        subtitle="Earn ANM for proof-of-work and for AI — contributing data, training a shared open model, and serving it for inference — all from a single process. ANM-only payouts, wallet auto-created."
      >
        <Link href="/download" className="btn-primary">Download the GUI miner</Link>
        <Link href="/training-pools" className="btn-secondary">Join a training pool</Link>
        <Link href="/about-ena" className="btn-ghost">What is ENA?</Link>
        <Link href="/mine" className="btn-ghost">Mine from the CLI</Link>
      </Hero>

      {/* The one-command snippet — the headline of the whole product. */}
      <Section
        eyebrow="Get started"
        title="Two lines. That's the whole setup."
        description="Install the CLI and bring everything up. It auto-creates an Animica wallet, downloads the right binaries for your OS, and starts mining + AI useful-work at once."
      >
        <div className="card overflow-hidden">
          <div className="mb-3 flex items-center gap-2">
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="h-3 w-3 rounded-full bg-white/15" />
            <span className="ml-2 text-xs text-white/40">terminal</span>
          </div>
          <pre className="overflow-x-auto rounded-xl bg-black/50 p-4 font-mono text-sm leading-relaxed text-neon-green">
{`pip install --upgrade animica
animica up`}
          </pre>
          <p className="mt-4 text-sm text-white/60">
            One command does it all: PoW + AI useful-work, training, and serving — paid in ANM to a
            wallet it creates for you. No payout config, no second coin to manage.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link href="/mine" className="btn-secondary">CLI setup</Link>
            <Link href="/download" className="btn-ghost">Or download a GUI miner</Link>
            <Link href="/stats" className="btn-ghost">Live stats</Link>
          </div>
        </div>
      </Section>

      {/* The real pillars. */}
      <Section
        eyebrow="Why Animica"
        title="Useful-work, not wasted work"
        description="Mining funds an open AI model that anyone can train, serve, and use — and everything settles to your Animica address."
      >
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {PILLARS.map((p) => (
            <Pillar key={p.title} title={p.title} body={p.body} />
          ))}
        </div>
      </Section>

      {/* Closing CTA band. */}
      <Section>
        <div className="card flex flex-col items-start gap-4 md:flex-row md:items-center md:justify-between">
          <div className="space-y-1.5">
            <h2 className="text-xl font-semibold tracking-tight text-white">
              Ready to put your machine to <span className="grad-text">useful work</span>?
            </h2>
            <p className="text-sm text-white/60">
              Start on the CLI in two lines, or grab the one-click desktop app.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link href="/download" className="btn-primary">Download</Link>
            <Link href="/training-pools" className="btn-secondary">Training pools</Link>
            <Link href="/mine" className="btn-ghost">Mine</Link>
          </div>
        </div>
      </Section>
    </div>
  );
}

/**
 * Render a pillar body where text wrapped in backticks becomes inline code,
 * so we can highlight `animica up` without dangerouslySetInnerHTML.
 */
function Pillar({ title, body }: { title: string; body: string }) {
  const parts = body.split(/(`[^`]+`)/g);
  return (
    <div className="card group h-full">
      <h3 className="text-lg font-medium text-white">{title}</h3>
      <p className="mt-3 text-sm leading-relaxed text-white/65">
        {parts.map((part, i) =>
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
        )}
      </p>
    </div>
  );
}
