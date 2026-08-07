'use client';
import { useState } from 'react';

// Share an app / profile: native share sheet where available, copy-link, and an X intent.
// Small client island — everything else on the page stays server-rendered.

export default function ShareButton({ url, title, text }: { url: string; title: string; text?: string }) {
  const [copied, setCopied] = useState(false);
  const [canNative] = useState(() => typeof navigator !== 'undefined' && typeof navigator.share === 'function');

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard unavailable (http / old browser): select-and-copy via prompt as a last resort.
      window.prompt('Copy this link', url);
    }
  }

  async function native() {
    try {
      await navigator.share({ url, title, text });
    } catch {
      /* user dismissed the sheet */
    }
  }

  const tweet = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text || title)}&url=${encodeURIComponent(url)}`;

  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {canNative ? (
        <button className="btn ghost" style={{ minHeight: 44 }} onClick={native}>
          Share
        </button>
      ) : null}
      <button className="btn ghost" style={{ minHeight: 44 }} onClick={copy} aria-live="polite">
        {copied ? '✓ Copied' : 'Copy link'}
      </button>
      <a className="btn ghost" style={{ minHeight: 44 }} href={tweet} target="_blank" rel="noopener noreferrer">
        Post on X
      </a>
    </div>
  );
}
