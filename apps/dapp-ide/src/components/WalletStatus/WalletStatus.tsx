import { useState, useEffect } from "react";

export default function WalletStatus() {
  const [connected, setConnected] = useState(false);
  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);

  useEffect(() => {
    checkWalletConnection();
  }, []);

  const checkWalletConnection = async () => {
    if (typeof window !== "undefined" && (window as any).animica) {
      try {
        const accounts = await (window as any).animica.animica_accounts();
        if (accounts && accounts.length > 0) {
          setConnected(true);
          setAccount(accounts[0]);
          const chain = await (window as any).animica.animica_chainId();
          setChainId(chain);
        }
      } catch (error) {
        console.error("Failed to check wallet connection:", error);
      }
    }
  };

  const handleConnect = async () => {
    if (typeof window !== "undefined" && (window as any).animica) {
      try {
        const accounts = await (window as any).animica.animica_requestAccounts();
        if (accounts && accounts.length > 0) {
          setConnected(true);
          setAccount(accounts[0]);
          const chain = await (window as any).animica.animica_chainId();
          setChainId(chain);
        }
      } catch (error) {
        console.error("Failed to connect wallet:", error);
      }
    } else {
      alert("Animica wallet extension not detected. Please install it first.");
    }
  };

  if (!connected) {
    return (
      <button onClick={handleConnect}>
        Connect Wallet
      </button>
    );
  }

  return (
    <div style={{ 
      display: "flex", 
      alignItems: "center", 
      gap: "0.5rem",
      fontSize: "0.9rem"
    }}>
      <span>🟢</span>
      <span>{account?.slice(0, 6)}...{account?.slice(-4)}</span>
      {chainId && <span style={{ color: "#666" }}>Chain: {chainId}</span>}
    </div>
  );
}
