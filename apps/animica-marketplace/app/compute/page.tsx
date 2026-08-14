import type { Metadata } from 'next';
import { fleetStats } from '@/lib/cloud/dispatch';
import { formatAnm } from '@/lib/nanm';

// The "become a compute provider" page (§23, §24). Every number rendered here is a live DB
// aggregate from fleetStats() — §23 explicitly forbids invented provider statistics, so an
// empty network shows an honest empty state, not marketing zeros dressed up as traction.

export const metadata: Metadata = {
  title: 'Animica Compute — get paid ANM to run Python Cloud jobs',
  description:
    'Join the Animica Python Cloud provider network: your machine claims sandboxed Python executions, runs them in the same hardened Docker contract the gateway uses, and is paid a share of every execution in real, spendable ANM.',
  alternates: { canonical: 'https://animica.dev/compute' },
  openGraph: {
    title: 'Animica Compute — get paid ANM to run Python Cloud jobs',
    description:
      'Run the Animica cloud worker, claim sandboxed Python jobs, get paid a share of every execution in spendable ANM — settled the moment your result lands.',
    url: 'https://animica.dev/compute',
    siteName: 'Animica',
    type: 'website',
  },
};

export const dynamic = 'force-dynamic';

function fmtMemory(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB`;
  return `${mb} MB`;
}

export default async function ComputePage() {
  const s = await fleetStats();
  const sharePct = (s.providerShareBps / 100).toFixed(s.providerShareBps % 100 === 0 ? 0 : 1);
  const networkEmpty = s.providersOnline === 0;

  return (
    <div className="wrap" style={{ paddingTop: 44 }}>
      <div style={{ textAlign: 'center', maxWidth: 760, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>⚙️ Animica Python Cloud · provider network</div>
        <h1 style={{ fontSize: 44, letterSpacing: '-0.035em', lineHeight: 1.05 }}>
          Sell your compute.{' '}
          <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
            Get paid in ANM.
          </span>
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 12 }}>
          Developers deploy Python functions to Animica and customers pay ANM per execution. Your
          machine claims those jobs, runs them in the <b>same hardened Docker sandbox contract the
          gateway itself uses</b>, and earns <b>{sharePct}% of every execution&apos;s price</b> — credited
          as real, spendable ledger balance the moment your result settles. Not an IOU.
        </p>
      </div>

      {/* Live network stats — real DB aggregates, never invented */}
      <section className="section">
        <h2>The network right now</h2>
        <p className="sub">Live from the dispatch queue and the settlement ledger.</p>
        {networkEmpty && s.jobsCompleted === 0 ? (
          <div className="panel">
            <b>No providers are online yet.</b>
            <p className="muted" style={{ marginTop: 6 }}>
              The provider network is open and the queue is real, but nobody is serving it right now —
              jobs currently run on Animica&apos;s own gateway sandbox. Be the first machine on the
              fleet: the setup below takes about five minutes, and early providers face zero
              competition for every fleet-eligible job.
            </p>
          </div>
        ) : (
          <div className="panel">
            <div className="kpi">
              <div className="k"><b>{s.providersOnline}</b><span>providers online</span></div>
              <div className="k"><b>{s.providersRegistered}</b><span>registered all-time</span></div>
              <div className="k"><b>{s.cpuCoresOnline}</b><span>CPU cores online</span></div>
              <div className="k"><b>{fmtMemory(s.memoryMbOnline)}</b><span>memory online</span></div>
              <div className="k"><b>{s.gpusOnline}</b><span>GPUs online</span></div>
              <div className="k"><b>{s.jobsInFlight}</b><span>jobs in flight</span></div>
              <div className="k"><b>{s.jobsPending}</b><span>jobs queued</span></div>
              <div className="k"><b>{s.jobsCompleted.toLocaleString()}</b><span>jobs completed</span></div>
              <div className="k"><b>{formatAnm(s.paidToProvidersNanm)} ANM</b><span>paid to providers</span></div>
            </div>
            {networkEmpty && (
              <p className="muted" style={{ marginTop: 14, fontSize: 13.5 }}>
                No provider is online at this moment — completed totals above are historical. Start
                the worker below and the queue is yours.
              </p>
            )}
          </div>
        )}
      </section>

      {/* Earnings model */}
      <section className="section">
        <h2>How you earn</h2>
        <p className="sub">The split is policy-driven and enforced in one settlement transaction.</p>
        <div className="grid">
          <div className="card">
            <div className="top"><div className="ico">💰</div><div><h3>{sharePct}% of every execution</h3></div></div>
            <p>
              A customer pays a metered price per execution (base + CPU-ms + memory + egress). When
              your machine ran it, <b>{sharePct}% of that price is yours</b> — the current network
              policy&apos;s provider share, applied by the same exactly-once settlement that debits the
              customer and credits the developer. The parts always sum exactly to the price.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">⚡</div><div><h3>Paid at result time, spendable</h3></div></div>
            <p>
              Your payout posts to the append-only ledger as a <code className="inline">SALE_CREDIT</code>{' '}
              in the same database transaction that completes the job — real balance you can spend or
              withdraw, not a deferred IOU. Only revenue-bearing executions are dispatched to the
              fleet, so a completed job is always a paid job.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">🛡️</div><div><h3>Failures don&apos;t stiff you</h3></div></div>
            <p>
              If the developer&apos;s code raises or times out, the caller still pays the metered
              resource cost — and you still receive the provider share of it: you faithfully burned
              real compute. Only work you never deliver (crash, lease expiry) pays nothing, and the
              job is requeued for another provider.
            </p>
          </div>
        </div>
      </section>

      {/* Requirements */}
      <section className="section">
        <h2>What your machine needs</h2>
        <div className="grid">
          <div className="card">
            <div className="top"><div className="ico">🐳</div><div><h3>Docker — non-negotiable</h3></div></div>
            <p>
              Jobs are <b>untrusted Python</b>. The worker runs each one in the same contract the
              gateway uses: <code className="inline">--network none</code>, read-only rootfs, all
              capabilities dropped, memory/CPU/pid caps, unprivileged user. The worker checks for
              Docker and the <code className="inline">anm-pycloud-runtime:1</code> image at startup and{' '}
              <b>refuses to run without them</b> — there is no unsandboxed fallback, by design.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">🖥️</div><div><h3>Hardware</h3></div></div>
            <p>
              Linux (x86_64 or arm64) with Docker, Python 3.9+ for the worker itself, 2+ CPU cores and
              2&nbsp;GB+ free RAM (each job is capped at 1&nbsp;GB and one CPU). A GPU is optional —
              advertise it and you&apos;ll be eligible for GPU job kinds as they ship. Stable outbound
              HTTPS to animica.dev; no inbound ports, no port forwarding.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">🔑</div><div><h3>An ANM payout address</h3></div></div>
            <p>
              Earnings land in the marketplace ledger account for the bech32m{' '}
              <code className="inline">anim1…</code> address you register with. Create one in the{' '}
              <a href="https://wallet.animica.org">Animica wallet</a> if you don&apos;t have one — key
              material never touches the worker; the address is purely where money goes.
            </p>
          </div>
        </div>
      </section>

      {/* Setup */}
      <section className="section">
        <h2>Start serving in three commands</h2>
        <p className="sub">
          The worker self-registers with a locally generated bearer token (the server stores only its
          hash), builds the exact sandbox image the gateway runs, then claims jobs in a loop.
        </p>
        <div className="panel">
          <pre className="inline" style={{ display: 'block', padding: 14, borderRadius: 10, overflowX: 'auto', margin: 0 }}>
{`pip install -U animica
python -m animica.cloud_worker build-image --gateway https://animica.dev
python -m animica.cloud_worker run --gateway https://animica.dev --address anim1YOUR_PAYOUT_ADDRESS`}
          </pre>
          <p className="muted" style={{ marginTop: 12, fontSize: 13.5 }}>
            <code className="inline">build-image</code> fetches the sandbox build context
            (Dockerfile + runner) from{' '}
            <code className="inline">/api/cloud/v1/providers/runtime</code>, verifies the sha3-256
            digests, and runs <code className="inline">docker build</code> locally — you can read every
            line before you build. Add <code className="inline">--name</code> to label your machine,{' '}
            <code className="inline">--gpu &quot;RTX 4090&quot;</code> to advertise a GPU, and{' '}
            <code className="inline">--once</code> to serve a single job as a test. The token persists
            in <code className="inline">~/.animica/cloud-worker.json</code>.
          </p>
        </div>
      </section>

      {/* How it works / honesty */}
      <section className="section">
        <h2>How dispatch works — and what never reaches your machine</h2>
        <div className="grid">
          <div className="card">
            <div className="top"><div className="ico">📥</div><div><h3>Claim → run → settle</h3></div></div>
            <p>
              Your worker polls <code className="inline">/providers/claim</code>; the queue hands out
              each job exactly once (row-locked claim), with a {Math.round(s.leaseSeconds / 60)}-minute
              lease your heartbeats extend. You post the result, the execution settles, your share is
              credited — all in the same request.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">🔒</div><div><h3>Pure compute only</h3></div></div>
            <p>
              Only capability-free, secret-free functions are fleet-eligible. Developer secrets,
              wallet operations, AI calls and chain access are brokered by the gateway and{' '}
              <b>never leave it</b> — your machine sees the function source, the request payload, and
              nothing else. Executions are anchored on-chain at deploy time (source hash + artifact
              hash + DA blob id inside a signed DEPLOY tx) and executed off-chain in this hardened
              container.
            </p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">⚖️</div><div><h3>Reputation, not paperwork</h3></div></div>
            <p>
              Registration is open — no forms, no approval queue. Completed jobs raise your
              reputation; failures lower it, and a provider that keeps failing is automatically
              suspended from claiming. Priority customers&apos; jobs sit higher in the queue; the
              per-developer admission cap keeps any one developer from monopolizing the fleet.
            </p>
          </div>
        </div>
      </section>

      <section className="section" style={{ textAlign: 'center', paddingBottom: 70 }}>
        <a className="btn primary" href="/api/cloud/v1/providers">See the live network API</a>{' '}
        <a className="btn" href="/docs/cloud" style={{ marginLeft: 8 }}>Python Cloud docs</a>
      </section>
    </div>
  );
}
