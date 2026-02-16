"use client";

import { useMemo, useState } from "react";
import { requestAccounts } from "@/src/shared/wallet";

type StartResponse = {
  requestId: string;
  deepLink: string;
  universalLink: string;
  expiresAt: string;
};

export function WalletConnectModal({ open, onClose, onConnected }: { open: boolean; onClose: () => void; onConnected: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<StartResponse | null>(null);

  const qrSrc = useMemo(() => {
    if (!pending?.universalLink) return "";
    return `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(pending.universalLink)}`;
  }, [pending]);

  if (!open) return null;

  async function connectMobileWallet() {
    setLoading(true);
    setError(null);
    const res = await fetch("/api/wallet/connect/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ chainId: 1 }) });
    const data = await res.json();
    if (!res.ok) {
      setError(data.error ?? "Unable to start connect flow");
      setLoading(false);
      return;
    }

    const nextPending = {
      requestId: data.requestId,
      deepLink: data.deepLink,
      universalLink: data.universalLink,
      expiresAt: data.expiresAt
    };
    setPending(nextPending);
    window.location.href = nextPending.deepLink;

    const timer = window.setInterval(async () => {
      const poll = await fetch(`/api/wallet/connect/status?requestId=${nextPending.requestId}`);
      const pollData = await poll.json();
      if (pollData.status === "approved") {
        window.clearInterval(timer);
        navigator.vibrate?.(30);
        onConnected();
        onClose();
      }
      if (["rejected", "expired"].includes(pollData.status)) {
        window.clearInterval(timer);
        setError(`Wallet request ${pollData.status}`);
      }
    }, 2000);

    setLoading(false);
  }

  async function mockApprove() {
    if (!pending) return;
    const res = await fetch("/api/wallet/mock-approve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ requestId: pending.requestId }) });
    const data = await res.json();
    if (!res.ok) {
      setError(data.error ?? "Mock approve failed");
      return;
    }
    navigator.vibrate?.(20);
    onConnected();
    onClose();
  }

  async function connectExtension() {
    try {
      await requestAccounts();
      navigator.vibrate?.(20);
      onConnected();
      onClose();
    } catch (e) {
      setError((e as Error).message);
      navigator.vibrate?.([10, 30, 10]);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-950/90 p-4" role="dialog" aria-modal="true" aria-label="Connect wallet modal">
      <div className="mx-auto mt-12 max-w-md space-y-3 rounded-xl border border-slate-700 bg-slate-900 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Connect Wallet</h2>
          <button onClick={onClose} className="rounded px-2 py-1 text-sm">Close</button>
        </div>
        <button onClick={connectExtension} className="w-full rounded-lg border border-slate-600 p-3 text-left">Browser Extension</button>
        <button onClick={connectMobileWallet} disabled={loading} className="w-full rounded-lg border border-indigo-400 bg-indigo-500/20 p-3 text-left">Connect Animica Wallet (Mobile)</button>
        <div className="rounded-lg border border-amber-400/40 bg-amber-500/10 p-3 text-sm">
          Dev Signer (server key) is for testing only and should never be used for production funds.
        </div>

        {pending ? (
          <div className="space-y-2 rounded-lg border border-slate-700 p-3 text-sm">
            <p className="font-medium">Waiting for wallet approval…</p>
            <p className="text-xs text-slate-400">Open this link on mobile if auto-open fails:</p>
            <a className="block break-all text-xs text-blue-300 underline" href={pending.universalLink}>{pending.universalLink}</a>
            {qrSrc ? <img src={qrSrc} alt="Wallet connect QR code" className="mx-auto h-40 w-40 rounded bg-white p-1" /> : null}
            <button onClick={mockApprove} className="w-full rounded bg-slate-800 px-3 py-2 text-xs">Mock Approve (WALLET_MOCK=1)</button>
          </div>
        ) : null}

        {error ? <div className="rounded bg-rose-500/20 p-2 text-sm text-rose-200">{error}</div> : null}
      </div>
    </div>
  );
}
