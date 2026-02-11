import { useState } from "react";
import FileTree from "@/components/FileTree/FileTree";
import Editor from "@/components/Editor/Editor";
import BuildPanel from "@/components/BuildPanel/BuildPanel";
import WalletStatus from "@/components/WalletStatus/WalletStatus";

export default function IDE() {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      {/* Header */}
      <header style={{ 
        display: "flex", 
        justifyContent: "space-between", 
        alignItems: "center",
        padding: "0.5rem 1rem",
        borderBottom: "1px solid #ccc"
      }}>
        <h1 style={{ fontSize: "1.2rem" }}>Animica Dapp IDE</h1>
        <WalletStatus />
      </header>

      {/* Main content */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Sidebar - File Tree */}
        <aside style={{ 
          width: "250px", 
          borderRight: "1px solid #ccc",
          overflow: "auto"
        }}>
          <FileTree onFileSelect={setSelectedFile} />
        </aside>

        {/* Main editor area */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, overflow: "hidden" }}>
            <Editor filePath={selectedFile} />
          </div>
          
          {/* Bottom panel - Build output */}
          <div style={{ 
            height: "200px", 
            borderTop: "1px solid #ccc",
            overflow: "auto"
          }}>
            <BuildPanel />
          </div>
        </main>
      </div>
    </div>
  );
}
