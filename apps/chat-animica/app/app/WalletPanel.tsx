"use client";

import { useEffect, useState } from "react";
import { detectWallet } from "@/src/shared/wallet";
import { WalletConnectModal } from "./_components/WalletConnectModal";

type Session = { id: string; type: string; accounts: string[] };

export function WalletPanel() {
  const [state, setState] = useState<{ installed: boolean; chainId?: string }>({ installed: false });
  const [session, setSession] = useState<Session | null>(null);
  const [open, setOpen] = useState(false);

  async function refresh() {
    const walletState = await detectWallet().catch(() => ({ installed: false }));
    setState(walletState);
    const res = await fetch("/api/wallet/connect/status?requestId=latest").catch(() => null);
    if (res?.ok) {
      const data = await res.json();
      if (data.session) setSession(data.session);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <>
      <div className="card space-y-2 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Wallet</h3>
          <button onClick={() => setOpen(true)} className="rounded-lg bg-indigo-500 px-3 py-2 text-xs font-medium" aria-label="Open wallet connect modal">
            Connect Wallet
          </button>
        </div>
        <p>Extension: {state.installed ? `connected (chain ${state.chainId})` : "not detected"}</p>
        <p>Mobile Wallet Session: {session ? `${session.type} • ${session.accounts[0]}` : "not connected"}</p>
      </div>
      <WalletConnectModal open={open} onClose={() => setOpen(false)} onConnected={refresh} />
    </>
  );
}
