'use client';
import { useState } from 'react';

// "Get in the Animica Wallet" install CTA. Installing an app is a device operation: the Animica
// mobile wallet verifies the APK sha3-256 + signer cert against the catalog and the on-chain
// license before handing it to Android's PackageInstaller. The web storefront therefore hands off
// via a deep link into the wallet's Store tab (placeholder scheme until the wallet ships the
// intent filter) and shows the package identity so the handoff is transparent.

const DEEP_LINK = (slug: string) => `animica://store/apps/${slug}`;

export default function InstallCta({
  slug, packageName, priceText, playHref,
}: { slug: string; packageName?: string | null; priceText: string; playHref?: string }) {
  const [note, setNote] = useState('');

  function open() {
    setNote('Opening the Animica Wallet…');
    // Best-effort deep link; if no wallet handles it the page simply stays put.
    try { window.location.href = DEEP_LINK(slug); } catch { /* ignore */ }
    setTimeout(() => setNote("Don't have the wallet yet? Install it from animica.org/wallet, then reopen this app."), 1400);
  }

  return (
    <div>
      {/* Game Lab web game: play right here in the browser (a real, shareable URL) and — because the
          play page ships a per-game web manifest — "Add App" from the phone's Share menu to install
          it to the home screen as a PWA. Additive; only rendered when a play page is available, so
          non-game (APP/APK) listings are unchanged and keep the wallet CTA as their primary action. */}
      {playHref ? (
        <>
          <a className="btn primary" style={{ width: '100%', justifyContent: 'center' }} href={playHref}>
            ▶ Play
          </a>
          <div className="muted" style={{ fontSize: 12, marginTop: 10, textAlign: 'center' }}>
            Plays in your browser. On a phone, open the{' '}
            <a href={playHref}>play page</a> and choose <b>Add App</b> (Share → Add to Home Screen) to
            install it to your home screen — no App Store needed.
          </div>
          <div style={{ height: 1, background: 'var(--border)', margin: '14px 0' }} />
        </>
      ) : null}
      <button className={`btn ${playHref ? '' : 'primary'}`} style={{ width: '100%', justifyContent: 'center' }} onClick={open}>
        Get in the Animica Wallet
      </button>
      <div className="muted" style={{ fontSize: 12, marginTop: 10, textAlign: 'center' }}>
        {priceText} · installs & verifies on-device
      </div>
      {packageName ? (
        <div className="muted" style={{ fontSize: 11.5, marginTop: 8, textAlign: 'center' }}>
          <code className="inline">{packageName}</code>
        </div>
      ) : null}
      {note ? <div className="muted" style={{ fontSize: 12, marginTop: 10, textAlign: 'center' }}>{note}</div> : null}
      <a
        className="btn ghost"
        style={{ width: '100%', justifyContent: 'center', marginTop: 10 }}
        href="https://animica.org/wallet"
        target="_blank"
        rel="noreferrer"
      >
        Get the wallet
      </a>
    </div>
  );
}
