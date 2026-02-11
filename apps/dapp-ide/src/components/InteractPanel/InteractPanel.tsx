import { useState } from "react";

interface ABIFunction {
  name: string;
  stateMutability: "view" | "nonpayable" | "payable";
  inputs: { name: string; type: string }[];
  outputs: { name: string; type: string }[];
}

export default function InteractPanel() {
  const [contractAddress, setContractAddress] = useState<string>("");
  const [abi, setAbi] = useState<ABIFunction[]>([]);
  const [selectedFunction, setSelectedFunction] = useState<string>("");
  const [functionArgs, setFunctionArgs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string>("");

  const handleLoadABI = () => {
    // TODO: Load ABI from project or file upload
    const mockABI: ABIFunction[] = [
      {
        name: "getValue",
        stateMutability: "view",
        inputs: [],
        outputs: [{ name: "", type: "uint256" }],
      },
      {
        name: "setValue",
        stateMutability: "nonpayable",
        inputs: [{ name: "value", type: "uint256" }],
        outputs: [],
      },
    ];
    setAbi(mockABI);
  };

  const handleCall = async () => {
    setResult("Calling contract...");
    
    // TODO: Integrate with RPC client
    setTimeout(() => {
      setResult("Result: 42");
    }, 1000);
  };

  return (
    <div>
      <h2>Interact with Contract</h2>

      <div style={{ marginTop: "1rem" }}>
        <label>
          <strong>Contract Address:</strong>
          <input
            type="text"
            value={contractAddress}
            onChange={(e) => setContractAddress(e.target.value)}
            placeholder="0x..."
            style={{ 
              width: "100%", 
              marginTop: "0.5rem",
              padding: "0.5rem",
              fontFamily: "monospace"
            }}
          />
        </label>
      </div>

      <button onClick={handleLoadABI} style={{ marginTop: "1rem" }}>
        Load ABI
      </button>

      {abi.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <label>
            <strong>Function:</strong>
            <select
              value={selectedFunction}
              onChange={(e) => setSelectedFunction(e.target.value)}
              style={{ marginLeft: "1rem", padding: "0.25rem" }}
            >
              <option value="">-- Select Function --</option>
              {abi.map((fn) => (
                <option key={fn.name} value={fn.name}>
                  {fn.name} ({fn.stateMutability})
                </option>
              ))}
            </select>
          </label>

          {selectedFunction && (
            <div style={{ marginTop: "1rem" }}>
              <button onClick={handleCall}>
                Call {selectedFunction}
              </button>
            </div>
          )}
        </div>
      )}

      {result && (
        <div style={{ 
          marginTop: "1rem", 
          padding: "1rem",
          backgroundColor: "#f5f5f5",
          fontFamily: "monospace"
        }}>
          {result}
        </div>
      )}
    </div>
  );
}
