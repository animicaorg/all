import { useState } from "react";
import InteractPanel from "@/components/InteractPanel/InteractPanel";
import WalletStatus from "@/components/WalletStatus/WalletStatus";

export default function Interact() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <header style={{ 
        display: "flex", 
        justifyContent: "space-between", 
        alignItems: "center",
        padding: "0.5rem 1rem",
        borderBottom: "1px solid #ccc"
      }}>
        <h1 style={{ fontSize: "1.2rem" }}>Interact with Contract</h1>
        <WalletStatus />
      </header>

      <main style={{ flex: 1, padding: "2rem", overflow: "auto" }}>
        <InteractPanel />
      </main>
    </div>
  );
}
