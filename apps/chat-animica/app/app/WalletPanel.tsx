"use client";

import { useEffect, useState } from "react";
import { detectWallet } from "@/src/shared/wallet";

export function WalletPanel() {
  const [state, setState] = useState<{ installed: boolean; chainId?: string }>({ installed: false });

  useEffect(() => {
    detectWallet().then(setState).catch(() => setState({ installed: false }));
  }, []);

  if (!state.installed) {
    return (
      <div className="card text-sm">
        Animica Wallet not detected. <a className="underline" href="https://animica.org/wallet">Install Animica Wallet</a>
      </div>
    );
  }

  return <div className="card text-sm">Wallet connected. Chain ID: {state.chainId}</div>;
}
