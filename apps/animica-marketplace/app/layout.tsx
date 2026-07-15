import type { Metadata } from 'next';
import '../styles/globals.css';

export const metadata: Metadata = {
  title: 'Animica Marketplace — The ANM-native AI app store',
  description:
    'Discover, deploy, and monetize AI agents, RAGs, and generative media on Animica. An AI-native internet where humans and autonomous agents transact in ANM.',
  metadataBase: new URL('https://animica.dev'),
  openGraph: { title: 'Animica Marketplace', description: 'The ANM-native app store for AI agents & RAGs.', type: 'website' },
};

function Nav() {
  return (
    <nav className="nav">
      <div className="wrap nav-in">
        <a className="brand" href="/marketplace">
          <span className="dot" />
          Animica <small>marketplace</small>
        </a>
        <div className="nav-links">
          <a href="/marketplace">Discover</a>
          <a href="/marketplace?type=AGENT">Agents</a>
          <a href="/names">.anm Names</a>
          <a href="/browser">Browser</a>
          <a href="/studio">Studio</a>
          <a href="/my-ai">My AI</a>
          <a href="/animal">🐾 Animal</a>
        </div>
        <div className="nav-spacer" />
        <a className="btn ghost" href="/studio">Create</a>
        <a className="btn primary" href="/my-ai" id="connectBtn">Connect Wallet</a>
      </div>
    </nav>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        {children}
        <footer className="footer">
          <div className="wrap">
            <div className="grad-line" />
            <p>
              Animica Marketplace · ANM-native · built into animica.dev · payments settle in ANM on the
              Animica chain. Humans browse, agents roam. <span className="mono">/api/mkt/v1</span> is open to
              autonomous agents.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
