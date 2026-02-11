import { useState } from "react";

interface BuildOutput {
  status: "idle" | "building" | "success" | "error";
  message: string;
  diagnostics?: string[];
}

export default function BuildPanel() {
  const [output, setOutput] = useState<BuildOutput>({
    status: "idle",
    message: "Ready to build",
  });

  const handleBuild = async () => {
    setOutput({ status: "building", message: "Compiling contract..." });
    
    // TODO: Integrate with studio-wasm compiler
    setTimeout(() => {
      setOutput({
        status: "success",
        message: "Build successful",
        diagnostics: ["Contract compiled successfully", "Code hash: 0x123..."],
      });
    }, 1000);
  };

  return (
    <div style={{ padding: "1rem" }}>
      <div style={{ 
        display: "flex", 
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: "1rem"
      }}>
        <h3>Build Output</h3>
        <button onClick={handleBuild} disabled={output.status === "building"}>
          {output.status === "building" ? "Building..." : "Build"}
        </button>
      </div>

      <div style={{ 
        fontFamily: "monospace",
        fontSize: "0.9rem",
        whiteSpace: "pre-wrap"
      }}>
        <div style={{
          color: output.status === "error" ? "red" : 
                 output.status === "success" ? "green" : "black"
        }}>
          {output.message}
        </div>
        
        {output.diagnostics && (
          <div style={{ marginTop: "0.5rem" }}>
            {output.diagnostics.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
