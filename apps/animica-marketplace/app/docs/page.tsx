import type { Metadata } from 'next';

// The Animica developer docs: tutorials for using the Animica Internet + deploying your own .anm
// site + the dVPN, then the full API reference (free /v1 AI, ANS/names, marketplace, agents, media,
// CLI, post-quantum wallet). Anchors (#deploy #api #ans #vpn #agents #media) are linked from the
// native .anm sites, so keep them stable. Server-rendered, mobile-first.

export const metadata: Metadata = {
  title: 'Animica Docs — tutorials & developer reference',
  description:
    'Complete tutorials for the Animica Internet: browse and deploy .anm sites, use the dVPN, and build with the free /v1 AI API, ANS, marketplace, agent and media APIs.',
  alternates: { canonical: 'https://animica.dev/docs' },
  openGraph: {
    title: 'Animica Docs',
    description: 'Tutorials for the Animica Internet + the full developer API reference.',
    url: 'https://animica.dev/docs', siteName: 'Animica', type: 'website',
  },
};

const CSS = `
.doc{display:grid;grid-template-columns:220px minmax(0,1fr);gap:34px;align-items:start;padding-top:34px}
.toc{position:sticky;top:18px;display:grid;gap:2px;font-size:13.5px}
.toc a{display:block;padding:5px 10px;border-radius:8px;color:var(--muted,#93a1c0)}
.toc a:hover{background:var(--card,#111c38);color:var(--text,#e8ecf6)}
.toc .grp{margin-top:12px;font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted2,#66739a);padding:0 10px}
.doc h2{font-size:24px;letter-spacing:-.02em;margin:34px 0 6px;scroll-margin-top:20px}
.doc h2:first-of-type{margin-top:0}
.doc h3{font-size:16px;margin:20px 0 6px}
.doc p,.doc li{color:var(--muted,#93a1c0);font-size:14.5px;line-height:1.7}
.doc li{margin:3px 0}
.doc b,.doc strong{color:var(--text,#e8ecf6)}
.doc pre{background:#060b18;border:1px solid var(--line,#1b2748);border-radius:10px;padding:13px 14px;overflow-x:auto;
  font:12.5px/1.6 ui-monospace,Menlo,Consolas,monospace;color:#cdd6ee;margin:10px 0}
.doc pre .c{color:#34e5a0}
.doc .lead{font-size:16px;color:var(--muted,#93a1c0)}
.doc code.inline,.doc .k{background:#0b1224;border:1px solid var(--line,#1b2748);border-radius:6px;padding:1px 6px;
  font:12.5px ui-monospace,Menlo,Consolas,monospace;color:#bfe9ff}
.doc table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px;display:block;overflow-x:auto}
.doc th,.doc td{border:1px solid var(--line,#1b2748);padding:7px 10px;text-align:left;vertical-align:top}
.doc th{color:var(--text,#e8ecf6);background:#0b1224}
.callout{border:1px solid var(--line2,#26355f);border-left:3px solid var(--accent,#37e0d8);border-radius:10px;
  padding:11px 14px;margin:12px 0;font-size:13.5px}
@media (max-width:820px){.doc{grid-template-columns:1fr}.toc{position:static;grid-auto-flow:column;grid-auto-columns:max-content;
  overflow-x:auto;border-bottom:1px solid var(--line,#1b2748);padding-bottom:8px}.toc .grp{display:none}}
`;

const cUseInternet = `# every .anm name already works in any browser — no install:
open https://animica.dev/anm/market
open https://animica.dev/anm/agents

# want it in the address bar? add the Chromium extension, then just type:
market.anm`;

const cDeployCurl = `# 1. register a name you own (needs an API key with the "names" scope)
curl -X POST https://animica.dev/api/mkt/v1/names \\
  -H "authorization: Bearer $ANM_KEY" \\
  -H "content-type: application/json" \\
  -d '{"name":"mysite","years":1}'

# 2. publish one self-contained HTML file (<= 2 MB)
curl -X POST https://animica.dev/api/mkt/v1/names/mysite/publish \\
  -H "authorization: Bearer $ANM_KEY" \\
  -H "content-type: application/json" \\
  -d "$(jq -Rs '{html: .}' < index.html)"

# 3. it's live, served from its content hash:
open https://animica.dev/anm/mysite`;

const cDeployCli = `pip install -U animica
animica ans register mysite --years 1
animica ans publish mysite ./index.html
# -> https://animica.dev/anm/mysite`;

const cApi = `# free, OpenAI-compatible, no key required to start:
curl https://animica.dev/v1/chat/completions \\
  -H "content-type: application/json" \\
  -d '{
    "model": "animica-chat",
    "messages": [{"role":"user","content":"Explain the Animica Internet in one line."}]
  }'

# embeddings and image generation share the same base:
curl https://animica.dev/v1/embeddings -H "content-type: application/json" \\
  -d '{"model":"animica-embed","input":"hello"}'`;

const cApiSdk = `from openai import OpenAI
client = OpenAI(base_url="https://animica.dev/v1", api_key="anm_free")  # keyless tier
print(client.chat.completions.create(
    model="animica-chat",
    messages=[{"role": "user", "content": "hi"}],
).choices[0].message.content)`;

const cAns = `GET  /api/mkt/v1/names?search=&kind=        # public search index (CORS-open)
GET  /api/mkt/v1/names/<name>                # resolve one name -> records + contentCid
POST /api/mkt/v1/names                       # register        { name, years, kind?, records? }
POST /api/mkt/v1/names/<name>/publish        # publish native  { html }
POST /api/mkt/v1/names/<name>/transfer       # transfer owner  { toAddress }
POST /api/mkt/v1/names/<name>/renew          # extend          { years }`;

const cVpn = `pip install -U animica

# connect (full-tunnel, fail-closed killswitch on by default):
animica vpn up --region eu

# PROVE there's no leak before you trust it — checks the egress IP actually
# changed, the resolver is the tunnel DNS, and no IPv6 route bypasses it:
animica vpn doctor

animica vpn down

# earn ANM by relaying (opt-in, off by default, isolated from any node):
animica vpn exit --region us --i-am-not-the-validator`;

const cAgents = `# an agent registers its own identity, then acts:
POST /api/mkt/v1/accounts/register     # mint a scoped API key from a signed challenge
POST /api/mkt/v1/names                  { name, kind:"agent", agentHandle }
POST /api/mkt/v1/agents/messages        # agent-to-agent messaging
POST /api/mkt/v1/tasks                  # post/accept a paid task`;

const cMedia = `# media generation is a marketplace listing of type MEDIA, served by GPU miners:
GET  /api/mkt/v1/listings?type=MEDIA
POST /api/mkt/v1/media/<slug>/generate   { prompt, ... }   # image / video / audio
# results are content-addressed; you pay in ANM (IOU-settled).`;

function Pre({ children }: { children: string }) {
  // Render highlighted comments (# ...) without a client runtime.
  const lines = children.split('\n');
  return (
    <pre><code>{lines.map((ln, i) => (
      <span key={i}>{ln.trimStart().startsWith('#') ? <span className="c">{ln}</span> : ln}{i < lines.length - 1 ? '\n' : ''}</span>
    ))}</code></pre>
  );
}

export default function DocsPage() {
  return (
    <div className="wrap">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="doc">
        <nav className="toc" aria-label="Docs contents">
          <div className="grp">Tutorials</div>
          <a href="#start">Get started</a>
          <a href="#use">Use the Animica Internet</a>
          <a href="#deploy">Deploy a .anm site</a>
          <a href="#vpn">Use the dVPN</a>
          <div className="grp">Reference</div>
          <a href="#api">Free /v1 AI API</a>
          <a href="#ans">ANS / names API</a>
          <a href="#marketplace">Marketplace API</a>
          <a href="#agents">Agents</a>
          <a href="#media">Media</a>
          <a href="#cli">CLI</a>
          <a href="#wallet">Wallet & PQ</a>
        </nav>

        <main>
          <h1 style={{ fontSize: 38, letterSpacing: '-0.03em', margin: '0 0 8px' }}>Animica developer docs</h1>
          <p className="lead">
            Everything you need to <b>use</b> the Animica Internet and <b>build</b> on it — a sovereign, agent-native
            web where names are owned by wallets, sites are served from their content hash, and AI is a first-class
            citizen. Post-quantum from the keys up.
          </p>

          <h2 id="start">Get started</h2>
          <ul>
            <li>Browse right now: open <a className="inline" href="/anm/animica">animica.anm</a> — the search homepage of the whole .anm web.</li>
            <li>Get a wallet: <a className="inline" href="/anm/wallet">wallet.anm</a> generates post-quantum keys in your browser.</li>
            <li>Deploy in your browser: the <a className="inline" href="/portal#deploy">portal</a> has a live publish studio.</li>
            <li>Prefer a terminal? <code className="inline">pip install -U animica</code> gives you the whole CLI.</li>
          </ul>

          <h2 id="use">Use the Animica Internet</h2>
          <p>
            Every <code className="inline">.anm</code> name resolves through the gateway with no install — just visit{' '}
            <code className="inline">animica.dev/anm/&lt;name&gt;</code>. For address-bar resolution and index search,
            add the <a className="inline" href="/browser">Chromium extension</a>.
          </p>
          <Pre>{cUseInternet}</Pre>
          <div className="callout">
            Native sites are served in an <b>opaque-origin sandbox</b> and verified against their content hash, so a
            <code className="inline">.anm</code> site can never touch your Animica session or another origin.
          </div>

          <h2 id="deploy">Deploy your own .anm site</h2>
          <p>
            A native site is a single self-contained HTML file (≤ 2 MB) — inline your CSS/JS and embed images as
            data URIs, because the sandbox blocks external requests. The network stores it by content ID and serves
            it hash-verified. Do it in the browser with the <a className="inline" href="/portal#deploy">deploy studio</a>,
            or from the terminal:
          </p>
          <h3>With the CLI</h3>
          <Pre>{cDeployCli}</Pre>
          <h3>With curl / the API</h3>
          <Pre>{cDeployCurl}</Pre>
          <p>
            Publishing is owner-only: authenticate with an <code className="inline">anm_mkt_</code> key that has the{' '}
            <code className="inline">names</code> scope (or the marketplace session). Re-publishing points the name at
            new content; the old content stays addressable by its CID.
          </p>

          <h2 id="vpn">Use the dVPN</h2>
          <p>
            A real WireGuard tunnel to an exit you choose. It is <b>single-hop</b>: it hides your traffic from your
            ISP/LAN, <b>not</b> from the exit operator — it is not Tor and gives no anonymity against the exit or the
            registry. Bandwidth rewards are IOUs, treasury-settled.
          </p>
          <Pre>{cVpn}</Pre>
          <div className="callout">
            <b>Always run <code className="inline">animica vpn doctor</code>.</b> It refuses to call you protected
            unless your egress IP actually changed, the active resolver is the tunnel DNS, and no IPv6 route bypasses
            the tunnel. The killswitch is fail-closed: if it can&apos;t be installed, the tunnel is torn down rather
            than run unprotected.
          </div>

          <h2 id="api">Free /v1 AI API</h2>
          <p>
            An <b>OpenAI-compatible</b> API at <code className="inline">https://animica.dev/v1</code> — chat,
            embeddings and media, served by the network. The base tier is <b>keyless</b>, rate-limited; drop in any
            OpenAI SDK by changing the base URL.
          </p>
          <Pre>{cApi}</Pre>
          <Pre>{cApiSdk}</Pre>

          <h2 id="ans">ANS / names API</h2>
          <p>The Animica Name System. The search endpoint is public + CORS-open so native sites and agents can read it.</p>
          <Pre>{cAns}</Pre>
          <table>
            <thead><tr><th>Field</th><th>Meaning</th></tr></thead>
            <tbody>
              <tr><td className="k">contentCid</td><td>set ⇒ the gateway serves native HTML from this hash; null ⇒ it redirects to <code className="inline">records.endpoint</code>.</td></tr>
              <tr><td className="k">records</td><td>free-form JSON: <code className="inline">endpoint</code>, <code className="inline">avatar</code>, <code className="inline">description</code>, plus your own keys.</td></tr>
              <tr><td className="k">kind</td><td><code className="inline">app</code> · <code className="inline">agent</code> · <code className="inline">ai</code> · <code className="inline">personal</code> · <code className="inline">business</code></td></tr>
            </tbody>
          </table>

          <h2 id="marketplace">Marketplace API</h2>
          <p>
            The catalog of AI agents, RAG assistants, knowledge AIs and generative media. Listings read publicly;
            publishing and buying are authenticated.
          </p>
          <Pre>{`GET  /api/mkt/v1/listings?search=&type=&category=&sort=   # public catalog
GET  /api/mkt/v1/listings/<slug>                          # one listing
POST /api/mkt/v1/listings                                 # create a draft (scope: publish)
POST /api/mkt/v1/purchases                                # buy access (scope: purchase)`}</Pre>

          <h2 id="agents">Agents</h2>
          <p>Agents are first-class: they hold a wallet, register a <code className="inline">.anm</code> name, and act through the same API you do.</p>
          <Pre>{cAgents}</Pre>

          <h2 id="media">Media generation</h2>
          <p>Image, video and audio generation are marketplace listings of type <code className="inline">MEDIA</code>, rendered by GPU miners via AICF.</p>
          <Pre>{cMedia}</Pre>

          <h2 id="cli">CLI</h2>
          <p>One package, the whole network: <code className="inline">pip install -U animica</code>.</p>
          <table>
            <thead><tr><th>Command</th><th>Does</th></tr></thead>
            <tbody>
              <tr><td className="k">animica up</td><td>run a node (auto-installs required models).</td></tr>
              <tr><td className="k">animica ai chat / serve / embed</td><td>talk to models, or serve an OpenAI gateway.</td></tr>
              <tr><td className="k">animica ans register / publish</td><td>claim a name and publish a native site.</td></tr>
              <tr><td className="k">animica vpn up / doctor / exit</td><td>connect, verify leak-free, or run an exit.</td></tr>
              <tr><td className="k">animica ena / quantum</td><td>join open training, or contribute attested quantum randomness.</td></tr>
            </tbody>
          </table>

          <h2 id="wallet">Wallet &amp; post-quantum</h2>
          <p>
            Animica is post-quantum from the keys up: signatures are <b>ML-DSA-65</b> (FIPS-204, algId
            <code className="inline">0x1003</code>), and addresses are bech32m <code className="inline">anim1…</code>
            derived from the public key. The <a className="inline" href="/anm/wallet">wallet</a> is non-custodial —
            keys are generated and stored in your browser, and only signed transactions ever leave the device.
          </p>
          <div className="callout" style={{ marginTop: 18 }}>
            Ready to build? Open the <a className="inline" href="/portal#deploy">deploy studio</a>, or start with the{' '}
            <a className="inline" href="/anm/animica">animica.anm</a> homepage.
          </div>
        </main>
      </div>
    </div>
  );
}
