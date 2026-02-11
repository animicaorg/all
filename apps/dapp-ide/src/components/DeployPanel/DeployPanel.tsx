import { useState } from "react";

interface DeployState {
  status: "idle" | "deploying" | "success" | "error";
  txHash?: string;
  contractAddress?: string;
  error?: string;
}

export default function DeployPanel() {
  const [deployState, setDeployState] = useState<DeployState>({ status: "idle" });
  const [constructorArgs, setConstructorArgs] = useState<string>("");

  const handleDeploy = async () => {
    setDeployState({ status: "deploying" });

    // TODO: Integrate with wallet and SDK
    setTimeout(() => {
      setDeployState({
        status: "success",
        txHash: "0x1234567890abcdef",
        contractAddress: "0xabcdef1234567890",
      });
    }, 2000);
  };

  return (
    <div>
      <h2>Deploy Contract</h2>
      
      <div style={{ marginTop: "1rem" }}>
        <label>
          <strong>Constructor Arguments (JSON):</strong>
          <textarea
            value={constructorArgs}
            onChange={(e) => setConstructorArgs(e.target.value)}
            placeholder='{"param1": "value1"}'
            style={{
              width: "100%",
              height: "100px",
              marginTop: "0.5rem",
              fontFamily: "monospace",
            }}
          />
        </label>
      </div>

      <button
        onClick={handleDeploy}
        disabled={deployState.status === "deploying"}
        style={{ marginTop: "1rem" }}
      >
        {deployState.status === "deploying" ? "Deploying..." : "Deploy Contract"}
      </button>

      {deployState.status === "success" && (
        <div style={{ marginTop: "1rem", color: "green" }}>
          <p>✓ Contract deployed successfully!</p>
          <p>Transaction: {deployState.txHash}</p>
          <p>Contract Address: {deployState.contractAddress}</p>
        </div>
      )}

      {deployState.status === "error" && (
        <div style={{ marginTop: "1rem", color: "red" }}>
          <p>✗ Deployment failed: {deployState.error}</p>
        </div>
      )}
    </div>
  );
}
