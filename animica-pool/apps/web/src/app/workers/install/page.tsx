import Link from "next/link";

export default function WorkerInstallPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <header className="space-y-4">
        <span className="badge">Workers</span>
        <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-5xl">
          Connect a <span className="grad-text">worker</span>
        </h1>
        <p className="max-w-2xl text-lg text-white/65">
          Bring a CPU or GPU machine online in two steps and start earning from useful work.
        </p>
      </header>

      <section className="card space-y-5">
        <ol className="list-decimal space-y-4 pl-5 text-white/70">
          <li>
            Create a worker on the{" "}
            <Link href="/workers" className="text-neon-green hover:underline">Workers</Link>{" "}
            page and copy the one-time token.
          </li>
          <li>On your machine, run the installer:</li>
        </ol>
        <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/40 p-4 text-sm text-neon-green">{`curl -fsSL https://pool.animica.org/install-worker.sh | bash -s -- anm_worker_xxx`}</pre>
      </section>

      <section className="space-y-4">
        <div className="card">
          <h2 className="text-lg font-medium text-white">What the installer does</h2>
          <p className="mt-2 text-sm text-white/60">
            It checks Docker, collects hardware info, registers the machine, pulls Animica&apos;s
            controlled model-serving container, and starts a heartbeat. Your worker then moves through{" "}
            <code className="rounded bg-black/40 px-1.5 py-0.5 text-neon-green">pending → benchmarking → online</code>{" "}
            and earns according to its selected earning mode.
          </p>
        </div>
        <div className="card border-neon-green/20">
          <h2 className="text-lg font-medium text-white">Sandboxed by design</h2>
          <p className="mt-2 text-sm text-white/60">
            The agent only runs Animica&apos;s controlled container — it never executes arbitrary
            customer code on your machine.
          </p>
        </div>
      </section>
    </div>
  );
}
