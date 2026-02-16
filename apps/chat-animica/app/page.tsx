import Link from "next/link";

export default function LandingPage() {
  return (
    <section className="space-y-6">
      <h1 className="text-4xl font-bold">Animica Studio Web IDE</h1>
      <p className="text-slate-300">Chat-first IDE to generate, simulate, and deploy Animica smart contracts through a guarded backend pipeline.</p>
      <div className="flex gap-3">
        <Link className="rounded bg-indigo-500 px-4 py-2 text-white" href="/app">Open App</Link>
        <Link className="rounded border border-slate-700 px-4 py-2" href="/pricing">View Pricing</Link>
      </div>
    </section>
  );
}
