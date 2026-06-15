import Link from "next/link";

export default function Page() {
  return (
    <div className="space-y-10">
      <header className="space-y-4">
        <span className="badge">Developers</span>
        <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-5xl">
          <span className="grad-text">Docs</span>
        </h1>
        <p className="max-w-2xl text-lg text-white/65">
          API reference, integration guides, and everything you need to build on Animica Pool.
        </p>
      </header>

      <section className="grid gap-4 md:grid-cols-3">
        <Link href="/ai" className="card group flex flex-col gap-2">
          <h2 className="text-lg font-medium text-white">AI inference API</h2>
          <p className="text-sm text-white/60">
            OpenAI-compatible endpoints, models, pricing, and SDK quickstarts.
          </p>
          <span className="mt-auto pt-2 text-sm text-neon-green opacity-0 transition-opacity group-hover:opacity-100">
            Explore →
          </span>
        </Link>
        <Link href="/workers" className="card group flex flex-col gap-2">
          <h2 className="text-lg font-medium text-white">Workers</h2>
          <p className="text-sm text-white/60">
            Register a machine, mint a token, and bring compute online.
          </p>
          <span className="mt-auto pt-2 text-sm text-neon-green opacity-0 transition-opacity group-hover:opacity-100">
            Explore →
          </span>
        </Link>
        <Link href="/mine" className="card group flex flex-col gap-2">
          <h2 className="text-lg font-medium text-white">Mining</h2>
          <p className="text-sm text-white/60">
            One-command CLI setup for ANM, Monero, or dual mining.
          </p>
          <span className="mt-auto pt-2 text-sm text-neon-green opacity-0 transition-opacity group-hover:opacity-100">
            Explore →
          </span>
        </Link>
      </section>

      <section className="card">
        <p className="text-sm text-white/50">
          Full developer documentation is on the way. In the meantime, the in-app quickstarts on the{" "}
          <Link href="/ai" className="text-neon-green hover:underline">AI</Link> page cover the API surface.
        </p>
      </section>
    </div>
  );
}
