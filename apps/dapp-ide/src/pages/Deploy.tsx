import { useState } from "react";
import DeployPanel from "@/components/DeployPanel/DeployPanel";
import WalletStatus from "@/components/WalletStatus/WalletStatus";

export default function Deploy() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <header style={{ 
        display: "flex", 
        justifyContent: "space-between", 
        alignItems: "center",
        padding: "0.5rem 1rem",
        borderBottom: "1px solid #ccc"
      }}>
        <h1 style={{ fontSize: "1.2rem" }}>Deploy Contract</h1>
        <WalletStatus />
      </header>

      <main style={{ flex: 1, padding: "2rem", overflow: "auto" }}>
        <DeployPanel />
      </main>
    </div>
  );
}
