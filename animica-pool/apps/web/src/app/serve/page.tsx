import type { Metadata } from "next";
import { Section } from "@/components/ui/Section";
import ServeWorker from "./ServeWorker";

export const metadata: Metadata = {
  title: "Serve & Earn — run AI inference from your phone's browser",
  description:
    "Turn your phone (or any WebGPU browser) into an Animica inference worker. No install: the model runs in your browser, answers real chat jobs from the network, and your wallet is credited in ANM per job. Plug in, tap start, earn.",
  alternates: { canonical: "/serve" },
};

export default function ServePage() {
  return (
    <main className="mx-auto max-w-4xl space-y-16 px-4 py-12 md:py-16">
      <Section
        eyebrow="Serve &amp; Earn — zero install"
        title="Your phone is an AI miner now"
        description="This page turns any WebGPU browser — including a phone plugged in on a shelf — into an Animica inference worker. A small open model downloads once and runs entirely on your device; the page claims real chat jobs from the network, answers them locally, and credits your wallet in ANM per job won. Nothing to install, no keys on this page — just an address to pay."
      >
        <ServeWorker />
      </Section>

      <Section title="How it works" className="text-sm text-white/70">
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            <strong className="text-white">The model runs on your device.</strong> WebLLM compiles a
            quantized open model (Qwen 2.5, ~1&nbsp;GB download, cached by your browser) to WebGPU.
            Prompts and answers never leave your device except to deliver the finished answer to the
            job queue.
          </li>
          <li>
            <strong className="text-white">Jobs come from the shared AICF queue.</strong> The same
            queue every <code className="rounded bg-white/10 px-1">animica up</code> node serves —
            free-chat traffic from animica.dev and paid inference jobs. Your browser polls, claims,
            answers, submits.
          </li>
          <li>
            <strong className="text-white">Races keep quality honest.</strong> Jobs are replicated to
            several workers; the first good answer wins and is the one credited. A fast desktop GPU
            will usually beat your phone when both are online — and your phone still wins whenever
            it&apos;s the fastest (or only) worker awake.
          </li>
          <li>
            <strong className="text-white">Earnings are per-job IOUs.</strong> Each job won credits
            its full estimated cost to your address on the node&apos;s worker ledger
            (<code className="rounded bg-white/10 px-1">aicf.workerEarnings</code>). Settlement to
            on-chain ANM rides the AICF service carve as it settles network-wide.
          </li>
        </ol>
      </Section>

      <Section
        title="Prefer a terminal? The Termux lane"
        description="Same queue, same ledger, no browser: a dependency-free package that drives llama.cpp natively. Survives in a tmux session with the screen off (termux-wake-lock), and works on any Linux box the same way."
        className="text-sm text-white/70"
      >
        <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 font-mono text-sm text-white/90">
          <code>{`pkg install python llama-cpp     # Termux (Android)
pip install animica-serve
animica-serve --address anim1yourwallet`}</code>
        </pre>
        <ul className="mt-3 list-disc space-y-1.5 pl-5">
          <li><code className="rounded bg-white/10 px-1">--model qwen2.5-0.5b</code> for older / low-RAM phones · <code className="rounded bg-white/10 px-1">--charge-only</code> pauses while unplugged (needs the Termux:API app).</li>
          <li>Already running Ollama or a llama-server? <code className="rounded bg-white/10 px-1">--openai-url http://127.0.0.1:11434/v1 --openai-model qwen2.5:1.5b</code> reuses it — no download.</li>
          <li><code className="rounded bg-white/10 px-1">animica-serve earnings --address anim1…</code> prints your ledger any time.</li>
        </ul>
      </Section>

      <Section title="Requirements &amp; battery" className="text-sm text-white/70">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong className="text-white">WebGPU browser.</strong> Chrome / Edge on Android or
            desktop work out of the box. iPhone: Safari with WebGPU (iOS&nbsp;18+; enable in
            Settings → Safari → Advanced → Feature Flags if it reports unavailable). Roughly 3&nbsp;GB
            of free RAM for the default model — pick the 0.5B model on smaller phones.
          </li>
          <li>
            <strong className="text-white">Plugged in, screen on.</strong> Sustained inference is
            real work: leave the phone charging. The page holds a screen wake-lock while serving and
            (where the browser exposes battery state) pauses automatically when you unplug.
          </li>
          <li>
            <strong className="text-white">Background serving.</strong> The worker runs on a
            background thread, so on Android Chrome and desktop it keeps serving while you use other
            apps — leave &quot;keep serving in background&quot; on (it plays a near-silent tone so the
            browser doesn&apos;t freeze the tab). iOS suspends background tabs regardless: on iPhone,
            leave the tab open on the charger.
          </li>
        </ul>
      </Section>
    </main>
  );
}
