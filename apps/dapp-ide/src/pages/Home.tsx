import { Link } from "react-router-dom";

export default function Home() {
  return (
    <div style={{ padding: "2rem" }}>
      <h1>Animica Dapp IDE</h1>
      <p>Build, compile, deploy, and interact with Animica smart contracts</p>
      
      <div style={{ marginTop: "2rem" }}>
        <h2>Features</h2>
        <ul>
          <li>Monaco-based code editor with Python syntax highlighting</li>
          <li>In-browser contract compilation using studio-wasm</li>
          <li>Deploy contracts to Animica networks</li>
          <li>Interact with deployed contracts</li>
          <li>Project management with IndexedDB persistence</li>
          <li>Wallet integration via window.animica</li>
        </ul>
      </div>

      <div style={{ marginTop: "2rem", display: "flex", gap: "1rem" }}>
        <Link to="/ide">
          <button>Open IDE</button>
        </Link>
        <Link to="/deploy">
          <button>Deploy Contract</button>
        </Link>
        <Link to="/interact">
          <button>Interact with Contract</button>
        </Link>
        <Link to="/settings">
          <button>Settings</button>
        </Link>
      </div>
    </div>
  );
}
