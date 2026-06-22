interface Props {
  onAccept: () => void;
  onReject: () => void;
  busy?: boolean;
}

export function ApprovalBar({ onAccept, onReject, busy }: Props) {
  return (
    <div className="flex items-center gap-2 border-t border-border bg-elevated/40 px-3 py-2.5">
      <span className="mr-auto text-xs text-muted">ENA is waiting for your approval</span>
      <button className="btn-ghost btn-sm" onClick={onReject} disabled={busy}>
        Reject
      </button>
      <button className="btn-primary btn-sm" onClick={onAccept} disabled={busy}>
        Approve
      </button>
    </div>
  );
}
