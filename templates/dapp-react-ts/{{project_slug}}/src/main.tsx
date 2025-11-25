import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Missing #root element. Ensure index.html contains <div id=\"root\"></div>.");
}

createRoot(rootEl).render(<App />);
