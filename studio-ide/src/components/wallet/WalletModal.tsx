import { useEffect } from "react";
import { useWalletStore } from "@/state/wallet";

const WEB_WALLET_URL = "https://wallet.animica.org";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5 flex-none" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d={d} />
    </svg>
  );
}

// Wallet-type chooser. The injected `window.animica` provider is shared by the
// browser extension AND the mobile app's in-app browser, so both route through
// connect(); the web wallet opens wallet.animica.org.
export function WalletModal({ onClose }: { onClose: () => void }) {
  const { available, connecting, error, connect, detect } = useWalletStore();

  useEffect(() => {
    detect();
  }, [detect]);

  async function injected() {
    const ok = await connect();
    if (ok) onClose();
  }

  const Option = ({
    icon,
    title,
    desc,
    onClick,
    href,
    badge,
  }: {
    icon: string;
    title: string;
    desc: string;
    onClick?: () => void;
    href?: string;
    badge?: { text: string; ok: boolean };
  }) => {
    const inner = (
      <>
        <span className="grid h-10 w-10 flex-none place-items-center rounded-xl bg-accent/15 text-accent">
          <Icon d={icon} />
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="flex items-center gap-2">
            <span className="font-medium">{title}</span>
            {badge && (
              <span className={`chip !px-1.5 !py-0 text-[10px] ${badge.ok ? "text-ok" : "text-muted"}`}>
                {badge.text}
              </span>
            )}
          </span>
          <span className="block truncate text-xs text-muted">{desc}</span>
        </span>
        <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none text-muted" fill="none" stroke="currentColor" strokeWidth="2">
          {href ? <path d="M7 17 17 7M9 7h8v8" /> : <path d="m9 6 6 6-6 6" />}
        </svg>
      </>
    );
    const cls =
      "flex w-full items-center gap-3 rounded-xl border border-border bg-elevated p-3 text-sm hover:border-accent/60 disabled:opacity-50";
    return href ? (
      <a className={cls} href={href} target="_blank" rel="noreferrer">
        {inner}
      </a>
    ) : (
      <button className={cls} onClick={onClick} disabled={connecting}>
        {inner}
      </button>
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="safe-b w-full max-w-sm rounded-t-2xl border border-border bg-surface p-4 shadow-2xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Connect a wallet</h2>
          <button className="text-muted hover:text-fg" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        <div className="space-y-2">
          <Option
            icon="M9 3v18m-4-4h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2Z"
            title="Browser extension"
            desc="Animica wallet extension (desktop)"
            onClick={injected}
            badge={{ text: available ? "Detected" : "Not detected", ok: available }}
          />
          <Option
            icon="M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm4 17h2"
            title="Animica app"
            desc="Open this page in the app's browser, then connect"
            onClick={injected}
          />
          <Option
            icon="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a1 1 0 0 1 1 1v2M3 7.5V17a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1v-3M21 11.5h-4a2 2 0 0 0 0 4h4a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5Z"
            title="Web wallet"
            desc="wallet.animica.org"
            href={WEB_WALLET_URL}
          />
        </div>

        {connecting && <p className="mt-3 text-center text-xs text-muted">Waiting for wallet confirmation…</p>}
        {error && <p className="mt-3 text-sm text-danger">{error}</p>}
        {!available && !connecting && (
          <p className="mt-3 text-center text-[11px] leading-snug text-muted">
            No injected wallet detected — install the extension or open the Studio inside the Animica app's browser.
          </p>
        )}
      </div>
    </div>
  );
}
