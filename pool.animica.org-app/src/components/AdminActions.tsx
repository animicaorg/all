"use client";

import { useState } from "react";

const ACTIONS: { to: string; label: string; tone: string }[] = [
  { to: "COMPLETING", label: "Retry owner payout", tone: "bg-blue-600 hover:bg-blue-500" },
  { to: "REFUND_DUE", label: "Retry refund", tone: "bg-amber-600 hover:bg-amber-500" },
  { to: "COMPLETE", label: "Force complete", tone: "bg-green-700 hover:bg-green-600" },
  { to: "CANCELLED", label: "Cancel", tone: "bg-white/10 hover:bg-white/20" },
  { to: "FAILED", label: "Mark failed", tone: "bg-red-700 hover:bg-red-600" },
];

export function AdminActions({ rentalId }: { rentalId: string }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function act(to: string) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(`/app/api/admin/rentals/${rentalId}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ to }),
      });
      const data = await res.json();
      if (!res.ok) setMsg(data.error || "Action failed");
      else window.location.reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {ACTIONS.map((a) => (
          <button
            key={a.to}
            disabled={busy}
            onClick={() => act(a.to)}
            className={`rounded-lg px-3 py-1.5 text-sm text-white disabled:opacity-50 ${a.tone}`}
          >
            {a.label}
          </button>
        ))}
      </div>
      {msg && <p className="text-sm text-red-400">{msg}</p>}
    </div>
  );
}
