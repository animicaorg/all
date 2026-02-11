import { useState, useEffect } from "react";
import { Link } from "react-router-dom";

interface NetworkConfig {
  name: string;
  rpcUrl: string;
  chainId: number;
}

const DEFAULT_NETWORKS: NetworkConfig[] = [
  { name: "Local", rpcUrl: "http://127.0.0.1:8545/rpc", chainId: 1337 },
  { name: "Mainnet", rpcUrl: "http://144.126.133.21:8545/rpc", chainId: 1 },
];

export default function Settings() {
  const [networks, setNetworks] = useState<NetworkConfig[]>(DEFAULT_NETWORKS);
  const [selectedNetwork, setSelectedNetwork] = useState<string>("Local");

  return (
    <div style={{ padding: "2rem" }}>
      <Link to="/">← Back to Home</Link>
      
      <h1 style={{ marginTop: "1rem" }}>Settings</h1>

      <div style={{ marginTop: "2rem" }}>
        <h2>Network Configuration</h2>
        
        <div style={{ marginTop: "1rem" }}>
          <label>
            <strong>Active Network:</strong>
            <select 
              value={selectedNetwork} 
              onChange={(e) => setSelectedNetwork(e.target.value)}
              style={{ marginLeft: "1rem", padding: "0.25rem" }}
            >
              {networks.map((network) => (
                <option key={network.name} value={network.name}>
                  {network.name} ({network.rpcUrl})
                </option>
              ))}
            </select>
          </label>
        </div>

        <div style={{ marginTop: "2rem" }}>
          <h3>Available Networks</h3>
          <ul style={{ marginTop: "1rem" }}>
            {networks.map((network) => (
              <li key={network.name} style={{ marginBottom: "0.5rem" }}>
                <strong>{network.name}</strong><br />
                RPC: {network.rpcUrl}<br />
                Chain ID: {network.chainId}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
