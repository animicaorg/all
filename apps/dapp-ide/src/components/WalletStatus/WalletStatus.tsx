/**
 * Wallet Status Component
 * Displays connection status and provides connect/disconnect functionality
 */

import { useWallet } from "../../animica/wallet/adapter";

export default function WalletStatus() {
  const {
    isAvailable,
    isConnected,
    isConnecting,
    accounts,
    chainId,
    error,
    connect,
    disconnect,
  } = useWallet();

  const formatAddress = (address: string) => {
    if (!address) return "";
    return `${address.slice(0, 10)}...${address.slice(-8)}`;
  };

  const getNetworkName = (id: number | null) => {
    if (id === null) return "Unknown";
    switch (id) {
      case 1:
        return "Mainnet";
      case 1337:
        return "Local";
      default:
        return `Chain ${id}`;
    }
  };

  if (!isAvailable) {
    return (
      <div className="wallet-status error">
        <div className="status-icon">⚠️</div>
        <div className="status-text">
          <div className="status-title">Wallet Not Found</div>
          <div className="status-subtitle">
            Please install the Animica wallet extension
          </div>
        </div>
      </div>
    );
  }

  if (!isConnected) {
    return (
      <div className="wallet-status disconnected">
        <button
          onClick={connect}
          disabled={isConnecting}
          className="connect-button"
        >
          {isConnecting ? "Connecting..." : "Connect Wallet"}
        </button>
        {error && <div className="error-message">{error}</div>}
      </div>
    );
  }

  return (
    <div className="wallet-status connected">
      <div className="wallet-info">
        <div className="account-info">
          <div className="account-icon">👤</div>
          <div className="account-details">
            <div className="account-address" title={accounts[0]}>
              {formatAddress(accounts[0])}
            </div>
            <div className="network-name">{getNetworkName(chainId)}</div>
          </div>
        </div>
        <button onClick={disconnect} className="disconnect-button" title="Disconnect">
          ✕
        </button>
      </div>
      {error && <div className="error-message">{error}</div>}
    </div>
  );
}
