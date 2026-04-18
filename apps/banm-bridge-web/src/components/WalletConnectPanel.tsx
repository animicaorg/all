type Props = {
  account: string | null;
  isMetaMask: boolean;
  onConnect: () => Promise<void> | void;
  providerDetected: boolean;
};

export function WalletConnectPanel({ account, isMetaMask, onConnect, providerDetected }: Props) {
  return (
    <section className="section">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 13, color: "#486079" }}>EVM wallet</div>
          <div style={{ fontWeight: 600 }}>
            {account ? <span className="mono">{account}</span> : "Not connected"}
          </div>
        </div>
        <button className="btn primary" onClick={() => onConnect()}>
          {account ? "Reconnect" : "Connect MetaMask"}
        </button>
      </div>
      {!providerDetected && (
        <div className="warning" style={{ marginTop: 10 }}>
          No injected EIP-1193 provider detected. Install MetaMask or open this site inside MetaMask browser.
        </div>
      )}
      {providerDetected && !isMetaMask && (
        <div className="warning" style={{ marginTop: 10 }}>
          Connected provider is not MetaMask. EIP-1193 is supported, but this UI is optimized for MetaMask.
        </div>
      )}
      <div className="warning" style={{ marginTop: 10 }}>
        Custodial bridge. Wallet connection proves only the connected EVM address control.
      </div>
    </section>
  );
}

