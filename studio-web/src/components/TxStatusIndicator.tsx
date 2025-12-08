import React from "react";

/**
 * TxStatusIndicator — displays transaction lifecycle status with appropriate styling.
 * States: pending, confirming, confirmed, failed, rejected
 */

export type TxStatus = "pending" | "confirming" | "confirmed" | "failed" | "rejected";

interface TxStatusIndicatorProps {
  status: TxStatus;
  hash?: string;
  error?: string;
  blockNumber?: number;
  compact?: boolean;
  onCopyHash?: () => void;
}

const STATUS_CONFIG: Record<
  TxStatus,
  {
    label: string;
    icon: string;
    color: string;
    bgColor: string;
    borderColor: string;
  }
> = {
  pending: {
    label: "Pending",
    icon: "⏳",
    color: "var(--fg)",
    bgColor: "color-mix(in oklab, var(--accent) 12%, var(--surface))",
    borderColor: "color-mix(in oklab, var(--accent) 30%, transparent)",
  },
  confirming: {
    label: "Confirming",
    icon: "🔄",
    color: "var(--fg)",
    bgColor: "color-mix(in oklab, var(--primary) 12%, var(--surface))",
    borderColor: "color-mix(in oklab, var(--primary) 30%, transparent)",
  },
  confirmed: {
    label: "Confirmed",
    icon: "✅",
    color: "var(--fg)",
    bgColor: "color-mix(in oklab, var(--success) 12%, var(--surface))",
    borderColor: "color-mix(in oklab, var(--success) 30%, transparent)",
  },
  failed: {
    label: "Failed",
    icon: "❌",
    color: "var(--fg)",
    bgColor: "color-mix(in oklab, var(--danger) 12%, var(--surface))",
    borderColor: "color-mix(in oklab, var(--danger) 30%, transparent)",
  },
  rejected: {
    label: "Rejected",
    icon: "🚫",
    color: "var(--fg-muted)",
    bgColor: "color-mix(in oklab, var(--text-muted) 8%, var(--surface))",
    borderColor: "color-mix(in oklab, var(--text-muted) 20%, transparent)",
  },
};

function shortenHash(hash?: string): string {
  if (!hash) return "";
  if (hash.length <= 16) return hash;
  return `${hash.slice(0, 8)}...${hash.slice(-6)}`;
}

export default function TxStatusIndicator({
  status,
  hash,
  error,
  blockNumber,
  compact = false,
  onCopyHash,
}: TxStatusIndicatorProps) {
  const config = STATUS_CONFIG[status];

  const handleCopy = async () => {
    if (hash) {
      await navigator.clipboard.writeText(hash);
      onCopyHash?.();
    }
  };

  if (compact) {
    return (
      <div className="tx-status-compact">
        <span className="status-icon" aria-hidden="true">
          {config.icon}
        </span>
        <span className="status-label">{config.label}</span>

        <style>{`
          .tx-status-compact {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 999px;
            background: ${config.bgColor};
            border: 1px solid ${config.borderColor};
            color: ${config.color};
            font-size: 12px;
            font-weight: 600;
          }
          .status-icon {
            font-size: 14px;
            line-height: 1;
          }
          .status-label {
            white-space: nowrap;
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="tx-status-indicator">
      <div className="status-header">
        <span className="status-icon-large" aria-hidden="true">
          {config.icon}
        </span>
        <div className="status-info">
          <h4 className="status-title">{config.label}</h4>
          {error && <p className="status-error">{error}</p>}
        </div>
      </div>

      {hash && (
        <div className="status-hash">
          <span className="hash-label">Transaction Hash</span>
          <div className="hash-value">
            <code className="hash-code">{shortenHash(hash)}</code>
            <button className="hash-copy-btn" onClick={handleCopy} title="Copy full hash">
              📋
            </button>
          </div>
        </div>
      )}

      {blockNumber !== undefined && (
        <div className="status-block">
          <span className="block-label">Block</span>
          <span className="block-value">#{blockNumber.toLocaleString()}</span>
        </div>
      )}

      <style>{`
        .tx-status-indicator {
          background: ${config.bgColor};
          border: 1px solid ${config.borderColor};
          border-radius: 12px;
          padding: 16px 18px;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
          animation: status-fade-in 200ms ease-out;
        }

        @keyframes status-fade-in {
          from {
            opacity: 0;
            transform: translateY(4px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        .status-header {
          display: flex;
          align-items: flex-start;
          gap: 12px;
        }

        .status-icon-large {
          font-size: 28px;
          line-height: 1;
          flex-shrink: 0;
        }

        .status-info {
          flex: 1;
          min-width: 0;
        }

        .status-title {
          margin: 0 0 4px 0;
          font-size: 16px;
          font-weight: 700;
          color: ${config.color};
          letter-spacing: -0.01em;
        }

        .status-error {
          margin: 0;
          font-size: 13px;
          line-height: 1.5;
          color: var(--danger);
          opacity: 0.92;
        }

        .status-hash,
        .status-block {
          margin-top: 14px;
          padding-top: 14px;
          border-top: 1px solid color-mix(in oklab, var(--border) 50%, transparent);
        }

        .hash-label,
        .block-label {
          display: block;
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--fg-muted);
          margin-bottom: 6px;
        }

        .hash-value {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .hash-code {
          font-family: var(--font-mono);
          font-size: 13px;
          color: ${config.color};
          background: color-mix(in oklab, var(--surface-elev-2) 70%, transparent);
          padding: 6px 10px;
          border-radius: 8px;
          border: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
          flex: 1;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .hash-copy-btn {
          appearance: none;
          background: transparent;
          border: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
          padding: 6px 8px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 14px;
          line-height: 1;
          transition: background 120ms ease, border-color 120ms ease;
        }

        .hash-copy-btn:hover {
          background: color-mix(in oklab, var(--surface-elev-2) 40%, transparent);
          border-color: var(--border);
        }

        .hash-copy-btn:active {
          transform: translateY(1px);
        }

        .block-value {
          font-family: var(--font-mono);
          font-size: 14px;
          font-weight: 600;
          color: ${config.color};
        }

        @media (prefers-reduced-motion: reduce) {
          .tx-status-indicator {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
