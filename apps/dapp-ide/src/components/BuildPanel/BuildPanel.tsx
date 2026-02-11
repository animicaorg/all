import { useState } from "react";
import { useProjectStore } from "../../app/store";
import { compileSource } from "../../animica/vm/compiler";

interface BuildOutput {
  status: "idle" | "building" | "success" | "error";
  message: string;
  diagnostics?: string[];
  codeHash?: string;
  codeSize?: number;
  gasUpperBound?: number;
}

export default function BuildPanel() {
  const {
    files,
    activeFile,
    setBuilding,
    setBuildResult,
    setBuildError,
    lastBuild,
  } = useProjectStore();

  const [output, setOutput] = useState<BuildOutput>({
    status: "idle",
    message: "Ready to build. Click 'Build' to compile your contract.",
  });

  const handleBuild = async () => {
    try {
      setOutput({ status: "building", message: "Compiling contract..." });
      setBuilding(true);

      // Find Python file (contract.py or first .py file)
      let contractFile = Array.from(files.values()).find((f) =>
        f.path.includes("contract.py")
      );
      if (!contractFile) {
        contractFile = Array.from(files.values()).find((f) => f.type === "python");
      }

      if (!contractFile) {
        throw new Error("No Python contract file found. Create a .py file first.");
      }

      // Find manifest.json
      let manifestFile = Array.from(files.values()).find((f) =>
        f.path.includes("manifest.json")
      );
      
      let manifest: any;
      if (manifestFile) {
        try {
          manifest = JSON.parse(manifestFile.content);
        } catch (e) {
          throw new Error("Invalid manifest.json: " + (e as Error).message);
        }
      } else {
        // Create default manifest
        manifest = {
          manifestVersion: "1.0.0",
          encoding: "animica-manifest/1",
          package: {
            name: "Contract",
            version: "0.1.0",
          },
          target: {
            vm: "python",
            vmVersion: ">=1.0.0 <2.0.0",
            abiVersion: "1.0.0",
          },
          entrypoint: contractFile.path,
          code: {},
          abi: {},
          capabilities: {
            required: [],
            optional: [],
          },
          integrity: {
            codeHash: "",
            abiHash: "",
          },
        };
      }

      // Compile
      const result = await compileSource({
        source: contractFile.content,
        manifest,
        withBytes: true,
      });

      // Save build result
      const artifact = {
        ...result,
        timestamp: Date.now(),
      };
      setBuildResult(artifact);

      setOutput({
        status: "success",
        message: "✅ Build successful!",
        diagnostics: result.diagnostics || ["Compilation completed successfully"],
        codeHash: result.codeHash,
        codeSize: result.codeSize,
        gasUpperBound: result.gasUpperBound,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBuildError(message);
      setOutput({
        status: "error",
        message: "❌ Build failed",
        diagnostics: [message],
      });
    } finally {
      setBuilding(false);
    }
  };

  const formatBytes = (bytes?: number) => {
    if (!bytes) return "N/A";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  return (
    <div style={{ 
      padding: "1rem",
      height: "100%",
      display: "flex",
      flexDirection: "column"
    }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
        }}
      >
        <h3 style={{ margin: 0 }}>Build Output</h3>
        <button
          onClick={handleBuild}
          disabled={output.status === "building" || files.size === 0}
          style={{
            padding: "8px 16px",
            background: output.status === "building" ? "#ccc" : "#007bff",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: output.status === "building" ? "not-allowed" : "pointer",
            fontSize: "14px",
            fontWeight: "600",
          }}
        >
          {output.status === "building" ? "⏳ Building..." : "🔨 Build"}
        </button>
      </div>

      {/* Build artifacts info */}
      {lastBuild && output.status === "success" && (
        <div
          style={{
            padding: "12px",
            background: "#e8f5e9",
            border: "1px solid #4caf50",
            borderRadius: "4px",
            marginBottom: "1rem",
            fontSize: "13px",
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: "8px", color: "#2e7d32" }}>
            📦 Build Artifacts
          </div>
          <div style={{ fontFamily: "monospace", lineHeight: "1.6" }}>
            <div>
              <strong>Code Hash:</strong> {lastBuild.codeHash?.slice(0, 20)}...
            </div>
            <div>
              <strong>Size:</strong> {formatBytes(lastBuild.codeSize)}
            </div>
            {lastBuild.gasUpperBound && (
              <div>
                <strong>Gas Upper Bound:</strong> {lastBuild.gasUpperBound.toLocaleString()}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Output console */}
      <div
        style={{
          flex: 1,
          fontFamily: "monospace",
          fontSize: "13px",
          background: "#1e1e1e",
          color: "#d4d4d4",
          padding: "12px",
          borderRadius: "4px",
          overflow: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        <div
          style={{
            color:
              output.status === "error"
                ? "#f44336"
                : output.status === "success"
                ? "#4caf50"
                : output.status === "building"
                ? "#ff9800"
                : "#d4d4d4",
            marginBottom: "8px",
            fontWeight: 600,
          }}
        >
          {output.message}
        </div>

        {output.diagnostics && output.diagnostics.length > 0 && (
          <div style={{ marginTop: "12px" }}>
            {output.diagnostics.map((line, i) => (
              <div key={i} style={{ marginBottom: "4px" }}>
                {line}
              </div>
            ))}
          </div>
        )}

        {output.status === "idle" && files.size === 0 && (
          <div style={{ color: "#888", marginTop: "12px" }}>
            💡 Create a Python contract file to get started.
          </div>
        )}
      </div>
    </div>
  );
}
