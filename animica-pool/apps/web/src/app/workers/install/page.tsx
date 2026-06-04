export default function WorkerInstallPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <h1 className="text-2xl font-semibold">Connect a worker</h1>
      <ol className="list-decimal space-y-3 pl-5 text-white/70">
        <li>Create a worker on the <a href="/workers" className="text-neon-blue">Workers</a> page and copy the one-time token.</li>
        <li>On your CPU/GPU machine, run the installer:</li>
      </ol>
      <pre className="overflow-x-auto rounded-lg bg-black/40 p-3 text-sm text-neon-green">{`curl -fsSL https://pool.animica.org/install-worker.sh | bash -s -- anm_worker_xxx`}</pre>
      <p className="text-white/60">
        The installer checks Docker, collects hardware info, registers the machine, pulls Animica&apos;s
        controlled model-serving container, and starts a heartbeat. Your worker then moves through{" "}
        <code>pending → benchmarking → online</code> and can earn from mining, inference, and rentals
        per its earning mode.
      </p>
      <p className="text-sm text-white/40">
        Security: the MVP only runs Animica&apos;s controlled container — it never executes arbitrary
        customer code on your machine.
      </p>
    </div>
  );
}
