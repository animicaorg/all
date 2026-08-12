import type { Metadata } from 'next';
import { Code, K, Callout, Table, PageNav } from '../doc';
import { limits } from '@/lib/cloud/config';

export const metadata: Metadata = {
  title: 'Security — Animica Python Cloud',
  description: 'The threat model and the actual isolation: container hardening, mediated capabilities, verifiable deployments.',
};

const DOCKER = `docker run --rm -i
  --network none                    # no interface at all: no exfiltration, no pip, no SSRF
  --read-only                       # immutable rootfs; code bind-mounted read-only
  --cap-drop ALL
  --security-opt no-new-privileges
  --user 10001:10001                # non-root, no shell, no home
  --memory {mb}m --memory-swap {mb}m   # a real cap, not a swap invitation
  --cpus 1
  --pids-limit ${limits.maxPids}
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=${limits.maxTmpfsMb}m
  anm-pycloud-runtime:1`;

const SSRF = `animica.http.fetch enforcement (host-side):
  https only · no credentials in URLs · no IPv6 literals
  localhost / *.local / *.internal blocked
  private + loopback IP ranges blocked, re-checked AFTER DNS resolution
  redirects NOT followed (a 3xx to an internal host would defeat the checks)
  auth/cookie/host headers stripped · response capped at 512 KB · 1–30s timeout`;

export default function Page() {
  return (
    <>
      <h1>Security</h1>
      <p className="cd-lead">
        The threat model is explicit: deployed Python is treated as <b>hostile</b>. It may try to read
        server secrets, reach the wallet, mine, fork-bomb, exhaust RAM, escape the container, or lie about
        what it used. The defenses are layered, and none of them trust the guest.
      </p>

      <h2>Layer 1 — OS isolation (the boundary)</h2>
      <p>Every execution is one fresh container:</p>
      <Code title="the exact hardening applied per execution">{DOCKER}</Code>
      <ul>
        <li>
          The Docker socket, host filesystem, host environment and credentials are never mounted. The
          runtime image contains no Animica code, no credentials and no network clients.
        </li>
        <li>
          In-runner rlimits (address space, CPU, file size {`8 MB`}, open files 128, core dumps off) make
          runaway code fail fast and predictably; the cgroups are the hard stop.
        </li>
        <li>
          A wall-clock killer SIGKILLs the container at timeout — the platform never relies on the guest
          honoring its own deadline. Setuid/setgid bits are stripped from the entire image so{' '}
          <K>no-new-privileges</K> has nothing to bite on.
        </li>
      </ul>

      <h2>Layer 2 — mediated capabilities</h2>
      <p>
        Because the sandbox has no network, <b>every</b> privileged operation (AI, chain reads, payments,
        nested calls, outbound HTTP, state, secrets) is an RPC to the host broker over the runner&apos;s
        private stdio channel. The broker is the only party holding credentials, and it authorizes each
        call against <b>server-held</b> state: the deployment&apos;s declared capabilities, the caller&apos;s grant
        and its budget counters, the call depth, the remaining quotas. Nothing the guest sends is trusted
        for authorization. See <a href="/docs/cloud/capabilities">Capabilities</a>.
      </p>
      <Code title="outbound HTTP is the sharpest edge — its rules">{SSRF}</Code>

      <h2>Layer 3 — an unforgeable protocol</h2>
      <ul>
        <li>
          Before any user code is imported, the runner moves the control protocol onto private file
          descriptors and points stdout/stderr at a capture file — <K>print()</K> works, but guest output
          can never inject protocol frames (e.g. forge &quot;the payment succeeded&quot;).
        </li>
        <li>Frames additionally carry a per-execution random token, and results are length-capped.</li>
        <li>
          Billing uses the <b>host-measured</b> container wall time; guest-reported CPU numbers are
          recorded for observability only, because hostile code could understate them.
        </li>
      </ul>

      <h2>Layer 4 — deploy-time controls</h2>
      <ul>
        <li>
          AST-only static validation (never executes the code): import policy, dangerous-call and
          sandbox-escape patterns, entrypoint checks, secret-shaped-literal warnings. It fails closed — a
          broken validator blocks deployment rather than skipping the check.
        </li>
        <li>
          A platform denylist blocks known-bad code fingerprints (source and artifact SHA3) from ever
          deploying again.
        </li>
        <li>
          Immutability is verified: before anything is anchored or served, the stored version&apos;s hashes are
          recomputed from its snapshot — a mismatch aborts the deployment.
        </li>
      </ul>

      <h2>Verifiable deployments</h2>
      <p>
        Deployments are <b>anchored on-chain and executed off-chain</b>: the DA blob holds the canonical
        manifest + verbatim source (content-addressed — the blob id is the SHA3-256 of the bytes), and a
        signed DEPLOY (t=1) transaction binds owner, function, version, source hash, artifact hash and
        blob id. Anyone can fetch the blob, re-hash it, and verify exactly what code serves an endpoint.
        Animica consensus does not execute arbitrary Python — vm_py CALL transactions revert on mainnet by
        design (raw exec is fail-closed; enabling it would be node RCE).
      </p>

      <h2>Data protection</h2>
      <Table
        head={['data', 'protection']}
        rows={[
          ['secrets', 'AES-256-GCM sealed at rest; write-only API; injected per-execution as env values; stripped from logs before storage; never dispatched to fleet providers'],
          [<K key="s">animica.state</K>, 'AES-256-GCM sealed at rest; 16 KB/value, 200 keys/function'],
          ['logs', 'secret-redacted, size-capped, retention bounded by the developer’s plan and enforced by a janitor sweep'],
          ['anonymous callers', 'identified only by a salted hash — raw IPs are never stored'],
          ['money', 'append-only double-entry ledger; exactly-once settlement; nightly reconciliation that alerts on any invariant break and never silently “fixes”'],
        ]}
      />

      <h2>Abuse control</h2>
      <ul>
        <li>
          In-process burst limits in front of durable per-identity daily/monthly free-tier counters
          (atomic conditional increments — no race for the last free slot), plus a platform-wide free-tier
          cost ceiling.
        </li>
        <li>
          Global and per-account execution concurrency caps, a bounded queue, and per-hour deploy limits.
        </li>
        <li>
          Suspension flags at function, app and developer level are checked on every execution; reports
          feed a moderation queue; every admin action that touches money or availability is audit-logged.
        </li>
      </ul>

      <Callout tone="warn">
        Found a vulnerability? Report it privately via the enterprise contact on{' '}
        <a href="/docs/cloud/api">the API page</a> — please do not test against other users&apos; functions or
        balances.
      </Callout>
      <PageNav current="/docs/cloud/security" />
    </>
  );
}
