import React, { useEffect, useState } from "react";

/**
 * ProviderBanner — displays a prominent banner when the Animica wallet provider
 * is not detected, prompting the user to install the wallet extension.
 */

interface ProviderBannerProps {
  providerStatus: "unknown" | "available" | "unavailable";
  onDismiss?: () => void;
}

export default function ProviderBanner({ providerStatus, onDismiss }: ProviderBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Reset dismissed state if provider becomes available
    if (providerStatus === "available") {
      setDismissed(false);
    }
  }, [providerStatus]);

  const handleDismiss = () => {
    setDismissed(true);
    onDismiss?.();
  };

  if (providerStatus !== "unavailable" || dismissed) {
    return null;
  }

  return (
    <div className="provider-banner" role="alert">
      <div className="provider-banner-content">
        <span className="provider-banner-icon" aria-hidden="true">
          ⚠️
        </span>
        <div className="provider-banner-text">
          <h3 className="provider-banner-title">Wallet Not Detected</h3>
          <p className="provider-banner-message">
            To deploy, sign transactions, and interact with contracts, please install the Animica Wallet extension.
          </p>
        </div>
        <div className="provider-banner-actions">
          <a
            href="https://github.com/animicaorg/all/tree/main/wallet-extension"
            target="_blank"
            rel="noopener noreferrer"
            className="provider-banner-btn primary"
          >
            Install Wallet
          </a>
          <button className="provider-banner-btn secondary" onClick={handleDismiss}>
            Dismiss
          </button>
        </div>
      </div>

      <style>{`
        .provider-banner {
          position: fixed;
          top: 80px;
          left: 50%;
          transform: translateX(-50%);
          z-index: 200;
          max-width: 680px;
          width: calc(100% - 40px);
          background: linear-gradient(135deg, 
            color-mix(in oklab, var(--warning) 16%, var(--surface-elev-2)),
            color-mix(in oklab, var(--danger) 14%, var(--surface-elev-2))
          );
          border: 1px solid color-mix(in oklab, var(--warning) 40%, transparent);
          border-radius: 14px;
          padding: 18px 20px;
          box-shadow: 
            0 0 0 1px color-mix(in oklab, var(--warning) 10%, transparent),
            0 12px 40px rgba(0, 0, 0, 0.3),
            0 0 60px color-mix(in oklab, var(--warning) 18%, transparent);
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

        .provider-banner-content {
          display: flex;
          align-items: center;
          gap: 16px;
        }

        .provider-banner-icon {
          font-size: 32px;
          flex-shrink: 0;
          line-height: 1;
        }

        .provider-banner-text {
          flex: 1;
          min-width: 0;
        }

        .provider-banner-title {
          margin: 0 0 6px 0;
          font-size: 16px;
          font-weight: 700;
          color: var(--fg-strong);
          letter-spacing: -0.01em;
        }

        .provider-banner-message {
          margin: 0;
          font-size: 14px;
          line-height: 1.5;
          color: var(--fg);
          opacity: 0.92;
        }

        .provider-banner-actions {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-shrink: 0;
        }

        .provider-banner-btn {
          appearance: none;
          border: 1px solid transparent;
          padding: 8px 16px;
          border-radius: 10px;
          font-weight: 700;
          font-size: 14px;
          cursor: pointer;
          text-decoration: none;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          white-space: nowrap;
          transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
        }

        .provider-banner-btn.primary {
          background: linear-gradient(135deg, 
            color-mix(in oklab, var(--primary) 90%, white 10%), 
            color-mix(in oklab, var(--primary-700) 92%, white 8%)
          );
          color: var(--on-accent);
          border-color: color-mix(in oklab, var(--primary) 70%, transparent);
          box-shadow: 0 4px 12px color-mix(in oklab, var(--primary) 24%, transparent);
        }

        .provider-banner-btn.primary:hover {
          transform: translateY(-1px);
          box-shadow: 0 6px 16px color-mix(in oklab, var(--primary) 30%, transparent);
        }

        .provider-banner-btn.secondary {
          background: var(--surface-elev-2);
          color: var(--fg);
          border-color: var(--border-muted);
        }

        .provider-banner-btn.secondary:hover {
          background: var(--surface-elev-3);
          border-color: var(--border);
        }

        .provider-banner-btn:active {
          transform: translateY(0);
        }

        @media (max-width: 720px) {
          .provider-banner {
            top: 72px;
            width: calc(100% - 24px);
          }
          
          .provider-banner-content {
            flex-direction: column;
            align-items: flex-start;
            gap: 14px;
          }

          .provider-banner-actions {
            width: 100%;
            flex-direction: column;
            gap: 8px;
          }

          .provider-banner-btn {
            width: 100%;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .provider-banner {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
