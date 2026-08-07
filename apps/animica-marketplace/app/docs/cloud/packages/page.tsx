import type { Metadata } from 'next';
import { K, Callout, Table, Code, PageNav } from '../doc';

export const metadata: Metadata = {
  title: 'Supported packages — Animica Python Cloud',
  description: 'The exact package set available inside the Animica Python Cloud sandbox, and why.',
};

// This table mirrors sandbox/Dockerfile exactly — the image is the source of truth.
const PACKAGES: [string, string, string][] = [
  ['numpy', '2.1.3', 'numerical arrays'],
  ['pandas', '2.2.3', 'dataframes'],
  ['pyyaml', '6.0.2', 'YAML parsing (import yaml)'],
  ['python-dateutil', '2.9.0.post0', 'date parsing (import dateutil)'],
  ['orjson', '3.10.12', 'fast JSON'],
  ['jinja2', '3.1.4', 'templating'],
  ['markdown', '3.7', 'Markdown → HTML'],
  ['beautifulsoup4', '4.12.3', 'HTML parsing (import bs4)'],
  ['pillow', '11.0.0', 'image processing (import PIL)'],
  ['cryptography', '44.0.0', 'hashing, HMAC, symmetric/asymmetric crypto'],
  ['pydantic', '2.10.3', 'data validation'],
];

const DECLARE = `# declare packages when deploying a version:
{"source": "…", "entrypoint": "main", "packages": ["numpy", "pandas"]}`;

export default function Page() {
  return (
    <>
      <h1>Supported packages</h1>
      <p className="cd-lead">
        The sandbox runs Python 3.12 with the full standard library plus a curated, version-pinned package
        set baked into the runtime image. Nothing else is importable — and that is a security property, not
        a missing feature.
      </p>

      <h2>The package set</h2>
      <Table
        head={['package', 'version', 'use for']}
        rows={PACKAGES.map(([n, v, d]) => [<K key={n}>{n}</K>, v, d])}
      />
      <p>
        Declare what you use in the deploy request (both pip names and import names are accepted, e.g.{' '}
        <K>bs4</K> or <K>beautifulsoup4</K>). An unsupported package is refused at deploy time with a clear{' '}
        <K>422 unsupported_package</K> — turning what would be a guaranteed runtime <K>ImportError</K> into
        an immediate message. At most 16 packages per function.
      </p>
      <Code title="deploying with packages">{DECLARE}</Code>

      <h2>Why there is no HTTP client</h2>
      <p>
        <K>requests</K>, <K>httpx</K> and <K>aiohttp</K> are deliberately absent, and stdlib network modules
        (<K>socket</K>, <K>http</K>, <K>urllib</K>) are rejected by the validator. The sandbox container is
        started with <K>--network none</K>: there is no network interface inside it at all, so an HTTP
        client would have nothing to talk to. The <b>only</b> outbound path is{' '}
        <K>animica.http.fetch</K>, which the host performs on your behalf — https-only, SSRF-guarded
        (private/loopback/internal targets blocked, DNS re-checked at request time, redirects not
        followed), size-capped and metered as egress. See <a href="/docs/cloud/security">Security</a>.
      </p>

      <h2>Why pip at runtime is impossible</h2>
      <p>
        <K>pip install</K> needs the network, and the sandbox has none — by construction, not by policy.
        The validator additionally rejects <K>pip</K>/<K>setuptools</K>/<K>distutils</K> imports at deploy
        time so the failure is explained early. Packages exist in exactly one place: the runtime image
        (<K>sandbox/Dockerfile</K>), where they are version-pinned so every function on the platform runs
        the same audited set. The pinned versions are part of the canonical artifact manifest that gets
        anchored on-chain with your deployment.
      </p>

      <Callout>
        Need a package that is not here? The set is curated on request — additions ship as a new runtime
        image version, never as per-function installs. Ask via the{' '}
        <a href="/docs/cloud/api">reports/enterprise API</a> or the Developer Center.
      </Callout>

      <h2>Blocked stdlib modules</h2>
      <p>
        The deploy-time validator refuses modules that cannot work in the sandbox or only make sense as an
        attack, with the reason in the finding:
      </p>
      <ul>
        <li>
          <b>No network:</b> <K>socket</K>, <K>socketserver</K>, <K>http</K>, <K>urllib</K>, <K>ftplib</K>,{' '}
          <K>smtplib</K>, <K>telnetlib</K>
        </li>
        <li>
          <b>No subprocesses / native access:</b> <K>subprocess</K>, <K>ctypes</K>, <K>multiprocessing</K>,{' '}
          <K>fcntl</K>, <K>mmap</K>, <K>pty</K>, <K>tty</K>
        </li>
        <li>
          <b>Runtime integrity:</b> <K>resource</K> (limits are platform-enforced), <K>pdb</K>,{' '}
          <K>asyncio</K> (unsupported by the ABI), <K>eval</K>/<K>exec</K>/<K>compile</K>/<K>__import__</K>{' '}
          as calls, and sandbox-escape attribute patterns like <K>__subclasses__</K>/<K>__globals__</K>
        </li>
      </ul>
      <p>
        Everything else in the standard library — <K>json</K>, <K>re</K>, <K>math</K>, <K>statistics</K>,{' '}
        <K>datetime</K>, <K>hashlib</K>, <K>base64</K>, <K>csv</K>, <K>collections</K>, <K>itertools</K>,{' '}
        <K>decimal</K>, <K>random</K>, <K>time</K>, … — works normally.
      </p>
      <PageNav current="/docs/cloud/packages" />
    </>
  );
}
