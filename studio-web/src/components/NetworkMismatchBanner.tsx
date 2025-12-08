import React, { useState } from "react";

/**
 * NetworkMismatchBanner — displays when wallet network doesn't match Studio's selected network.
 * Provides a non-blocking warning with option to switch network in wallet.
 */

interface NetworkMismatchBannerProps {
  walletChainId: number;
  studioChainId: number;
  onSwitchNetwork?: () => Promise<void>;
  onDismiss?: () => void;
}

export default function NetworkMismatchBanner({
  walletChainId,
  studioChainId,
  onSwitchNetwork,
  onDismiss,
}: NetworkMismatchBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  const [switching, setSwitching] = useState(false);

  const handleSwitch = async () => {
    if (!onSwitchNetwork) return;
    setSwitching(true);
    try {
      await onSwitchNetwork();
    } catch (err) {
      console.error("Failed to switch network:", err);
    } finally {
      setSwitching(false);
    }
  };

  const handleDismiss = () => {
    setDismissed(true);
    onDismiss?.();
  };

  if (dismissed || walletChainId === studioChainId) {
    return null;
  }

  return (
    <div className="network-mismatch-banner" role="alert">
      <div className="banner-content">
        <span className="banner-icon" aria-hidden="true">
          🔄
        </span>
        <div className="banner-text">
          <h3 className="banner-title">Network Mismatch</h3>
          <p className="banner-message">
            Your wallet is connected to chain <strong>#{walletChainId}</strong> but Studio is set to{" "}
            <strong>#{studioChainId}</strong>. Switch networks to interact with contracts.
          </p>
        </div>
        <div className="banner-actions">
          {onSwitchNetwork && (
            <button className="banner-btn primary" onClick={handleSwitch} disabled={switching}>
              {switching ? "Switching…" : "Switch in Wallet"}
            </button>
          )}
          <button className="banner-btn secondary" onClick={handleDismiss}>
            Dismiss
          </button>
        </div>
      </div>

      <style>{`
        .network-mismatch-banner {
          position: fixed;
          top: 80px;
          left: 50%;
          transform: translateX(-50%);
          z-index: 190;
          max-width: 680px;
          width: calc(100% - 40px);
          background: linear-gradient(135deg, 
            color-mix(in oklab, var(--accent) 14%, var(--surface-elev-2)),
            color-mix(in oklab, var(--primary) 16%, var(--surface-elev-2))
          );
          border: 1px solid color-mix(in oklab, var(--accent) 35%, transparent);
          border-radius: 14px;
          padding: 16px 18px;
          box-shadow: 
            0 0 0 1px color-mix(in oklab, var(--accent) 12%, transparent),
            0 10px 36px rgba(0, 0, 0, 0.26),
            0 0 50px color-mix(in oklab, var(--accent) 16%, transparent);
          animation: banner-slide-down 250ms ease-out;
        }

        @keyframes banner-slide-down {
          from {
            opacity: 0;
            transform: translateX(-50%) translateY(-20px);
          }
          to {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
          }
        }

        .banner-content {
          display: flex;
          align-items: center;
          gap: 14px;
        }

        .banner-icon {
          font-size: 28px;
          flex-shrink: 0;
          line-height: 1;
        }

        .banner-text {
          flex: 1;
          min-width: 0;
        }

        .banner-title {
          margin: 0 0 5px 0;
          font-size: 15px;
          font-weight: 700;
          color: var(--fg-strong);
          letter-spacing: -0.01em;
        }

        .banner-message {
          margin: 0;
          font-size: 13px;
          line-height: 1.5;
          color: var(--fg);
          opacity: 0.92;
        }

        .banner-message strong {
          font-weight: 700;
          color: var(--accent-strong);
        }

        .banner-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
        }

        .banner-btn {
          appearance: none;
          border: 1px solid transparent;
          padding: 7px 14px;
          border-radius: 10px;
          font-weight: 700;
          font-size: 13px;
          cursor: pointer;
          text-decoration: none;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          white-space: nowrap;
          transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease, opacity 120ms ease;
        }

        .banner-btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .banner-btn.primary {
          background: linear-gradient(135deg, 
            color-mix(in oklab, var(--accent) 88%, white 12%), 
            color-mix(in oklab, var(--accent) 94%, white 6%)
          );
          color: var(--on-accent);
          border-color: color-mix(in oklab, var(--accent) 65%, transparent);
          box-shadow: 0 3px 10px color-mix(in oklab, var(--accent) 22%, transparent);
        }

        .banner-btn.primary:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 5px 14px color-mix(in oklab, var(--accent) 28%, transparent);
        }

        .banner-btn.secondary {
          background: var(--surface-elev-2);
          color: var(--fg);
          border-color: var(--border-muted);
        }

        .banner-btn.secondary:hover:not(:disabled) {
          background: var(--surface-elev-3);
          border-color: var(--border);
        }

        .banner-btn:active:not(:disabled) {
          transform: translateY(0);
        }

        @media (max-width: 720px) {
          .network-mismatch-banner {
            top: 72px;
            width: calc(100% - 24px);
          }
          
          .banner-content {
            flex-direction: column;
            align-items: flex-start;
            gap: 12px;
          }

          .banner-actions {
            width: 100%;
            flex-direction: column;
            gap: 8px;
          }

          .banner-btn {
            width: 100%;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .network-mismatch-banner {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
