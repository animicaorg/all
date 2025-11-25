import React from "react";

type Props = {
  isBusy?: boolean;
  onCreate: () => void;
  onImport: () => void;
};

export default function Welcome({ isBusy = false, onCreate, onImport }: Props) {
  return (
    <section className="ob-card" data-testid="welcome">
      <h1 className="ob-title">Welcome to Animica Wallet</h1>
      <p className="ob-subtitle">
        A deterministic, privacy-first wallet. Keys are generated and stored locally on your device.
      </p>

      <ul className="ob-bullets">
        <li>No server-side custody — you control the keys.</li>
        <li>Post-quantum signatures by default (Dilithium3 / SPHINCS+).</li>
        <li>Back up your recovery phrase and keep it offline.</li>
      </ul>

      <div className="ob-actions">
        <button
          className="btn primary"
          onClick={onCreate}
          disabled={isBusy}
          data-testid="btn-create"
        >
          Create a new wallet
        </button>
        <button
          className="btn"
          onClick={onImport}
          disabled={isBusy}
          data-testid="btn-import"
        >
          Import with recovery phrase
        </button>
      </div>

      <p className="ob-hint">
        By proceeding you agree to securely store your recovery phrase.{" "}
        <a href="https://animica.dev/security" target="_blank" rel="noreferrer">Security tips</a>
      </p>
    </section>
  );
}
