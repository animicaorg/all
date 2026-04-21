import { useEffect, useState } from "react";
import { connectWallet, getWalletAccounts, getAnimicaProvider } from "../lib/wallet";
import { shortAddr } from "../lib/format";

export function WalletBadge() {
  const [accounts, setAccounts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void getWalletAccounts().then(setAccounts).catch(() => setAccounts([]));

    const provider = getAnimicaProvider() as any;
    if (!provider?.on) return;

    const onChanged = (next: string[]) => {
      if (!Array.isArray(next)) return;
      setAccounts(next.map(String));
    };

    provider.on("accountsChanged", onChanged);
    return () => {
      if (provider.removeListener) {
        provider.removeListener("accountsChanged", onChanged);
      }
    };
  }, []);

  const connected = accounts.length > 0;

  return (
    <button
      className="wallet-btn"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const next = await connectWallet();
          setAccounts(next);
        } finally {
          setBusy(false);
        }
      }}
      type="button"
    >
      {connected ? `Connected ${shortAddr(accounts[0] ?? "")}` : busy ? "Connecting..." : "Connect Wallet"}
    </button>
  );
}
