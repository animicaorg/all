import type { Metadata } from 'next';
import '../styles/globals.css';
import NavClient, { type NavLinkItem } from '../components/cloud-public/NavClient';

export const metadata: Metadata = {
  metadataBase: new URL('https://animica.dev'),
  title: 'Animica Cloud — write Python, deploy to Animica, get paid',
  description:
    'Animica Python Cloud: deploy Python functions and apps. Deployments are anchored on-chain and executed off-chain in a hardened container; every call is metered and settled in ANM — you get paid when people use your code.',
  openGraph: {
    title: 'Animica Cloud',
    description: 'Write Python. Deploy to Animica. Get paid when people use it.',
    type: 'website',
    siteName: 'Animica',
    // Every page under this layout had NO og:image, so a share of /pricing — the page
    // whose whole job is to be linked — rendered as a bare text snippet in Slack,
    // Discord and every social preview. Per-page metadata still overrides this.
    images: [{ url: '/og.png', width: 1200, height: 630, alt: 'Animica' }],
  },
  twitter: {
    // Was defaulting to the small 'summary' card for the same reason.
    card: 'summary_large_image',
    images: ['/og.png'],
  },
};

// The site IA. /marketplace, /studio and /my-ai are retired; the public surface leads with
// the Python Cloud app marketplace.
const NAV_LINKS: NavLinkItem[] = [
  { href: '/ai', label: 'AI' },
  { href: '/apps', label: 'Apps' },
  { href: '/functions', label: 'Functions' },
  { href: '/agents', label: 'Agents' },
  { href: '/compute', label: 'Compute' },
  { href: '/developers', label: 'Developers' },
  { href: '/docs', label: 'Docs' },
  { href: '/pricing', label: 'Pricing' },
];
const NAV_CTA: NavLinkItem = { href: '/cloud', label: 'Deploy' };

function Nav() {
  return (
    <nav className="nav">
      <div className="wrap nav-in">
        <a className="brand" href="/">
          <span className="dot" />
          Animica <small>cloud</small>
        </a>
        <NavClient links={NAV_LINKS} cta={NAV_CTA} />
      </div>
    </nav>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <div className="grad-line" />
        <div className="foot-grid">
          <div>
            <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>Animica</div>
            <p style={{ margin: 0, lineHeight: 1.6 }}>
              Write Python. Deploy to Animica. Get paid when people use it. Deployments are anchored
              on-chain (source hash + artifact hash + DA blob id inside a signed DEPLOY tx) and
              executed off-chain in a hardened container. Payments settle in ANM on the Animica
              chain.
            </p>
          </div>
          <div>
            <div className="foot-h">Platform</div>
            <a href="/apps">App marketplace</a>
            <a href="/functions">Functions</a>
            <a href="/agents">Agents</a>
            <a href="/compute">Compute</a>
            <a href="/pricing">Pricing</a>
          </div>
          <div>
            <div className="foot-h">Developers</div>
            <a href="/docs">Docs</a>
            <a href="/cloud">Developer console</a>
            <a href="/developers">Developer directory</a>
            <a href="/names">.anm names</a>
          </div>
          <div>
            <div className="foot-h">Network</div>
            <a href="/ai">Free AI</a>
            <a href="/portal">Animica Internet</a>
            <a href="/browser">Browser</a>
            <a href="/vpn">dVPN</a>
            <a href="https://animica.org">Protocol — animica.org</a>
          </div>
        </div>
        <p style={{ marginTop: 14 }}>
          Humans browse, agents roam — <span className="mono">/api/cloud/v1</span> is open to
          autonomous agents.
        </p>
      </div>
    </footer>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        {children}
        <Footer />
      </body>
    </html>
  );
}
