import React, { useMemo, useState } from "react";

type Props = {
  onBack: () => void;
  onNext: () => void; // fired once import succeeds
};

type Algo = "dilithium3" | "sphincs_shake_128s";

function normWords(input: string): string[] {
  return input
    .trim()
    .toLowerCase()
    .replace(/\u00A0/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

function validateMnemonic(words: string[]): string | null {
  if (words.length < 12) return "Recovery phrase looks too short (need at least 12 words).";
  if (words.length > 48) return "Recovery phrase looks too long.";
  if (!words.every((w) => /^[a-z]+$/.test(w))) return "Words must be letters a–z only.";
  return null;
}

async function bgCall<T = unknown>(message: any): Promise<T> {
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage(message, (resp: any) => {
        const err = chrome.runtime.lastError;
        if (err) return reject(new Error(err.message));
        if (resp && resp.ok) return resolve(resp.result as T);
        reject(new Error(resp?.error ?? "Import failed"));
      });
    } catch (e: any) {
      reject(e);
    }
  });
}

export default function Import({ onBack, onNext }: Props) {
  const [mnemonicText, setMnemonicText] = useState("");
  const [algo, setAlgo] = useState<Algo>("dilithium3");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [show, setShow] = useState(false);

  const words = useMemo(() => normWords(mnemonicText), [mnemonicText]);
  const mnemonicError = useMemo(() => validateMnemonic(words), [words]);
  const pwError = useMemo(() => {
    if (password.length < 8) return "Password must be at least 8 characters.";
    if (password !== confirm) return "Passwords do not match.";
    return null;
  }, [password, confirm]);

  const canImport = !mnemonicError && !pwError && !isBusy;

  async function onImport() {
    setError(null);
    setIsBusy(true);
    try {
      const res = await bgCall<{ address: string }>({
        type: "keyring/importMnemonic",
        mnemonic: words.join(" "),
        password,
        algo,
      });
      // Optionally you could stash address in state or store if onboarding needs it.
      void res;
      onNext();
    } catch (e: any) {
      setError(e?.message ?? "Failed to import. Please try again.");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <section className="ob-card" data-testid="import-mnemonic">
      <h1 className="ob-title">Import wallet</h1>
      <p className="ob-subtitle">Paste your recovery phrase, choose algorithm, and set a password to encrypt your vault.</p>

      {error && (
        <p className="ob-warning" role="alert">
          {error}
        </p>
      )}

      <div className="field">
        <label className="lbl">Recovery phrase</label>
        <textarea
          className={"mnemonic" + (mnemonicError ? " bad" : "")}
          rows={3}
          placeholder="twelve or twenty-four lowercase words separated by spaces"
          spellCheck={false}
          value={mnemonicText}
          onChange={(e) => setMnemonicText(e.target.value)}
        />
        <div className="row space-between">
          <small className="muted">
            {words.length} word{words.length === 1 ? "" : "s"}
          </small>
          {mnemonicError && <small className="msg">{mnemonicError}</small>}
        </div>
      </div>

      <div className="field">
        <label className="lbl">Signature algorithm</label>
        <div className="radio-row">
          <label className={"radio" + (algo === "dilithium3" ? " checked" : "")}>
            <input
              type="radio"
              name="algo"
              checked={algo === "dilithium3"}
              onChange={() => setAlgo("dilithium3")}
            />
            Dilithium3 (default)
          </label>
          <label className={"radio" + (algo === "sphincs_shake_128s" ? " checked" : "")}>
            <input
              type="radio"
              name="algo"
              checked={algo === "sphincs_shake_128s"}
              onChange={() => setAlgo("sphincs_shake_128s")}
            />
            SPHINCS+-SHAKE-128s
          </label>
        </div>
      </div>

      <div className="field">
        <label className="lbl">Password</label>
        <div className="pw-row">
          <input
            type={show ? "text" : "password"}
            placeholder="New password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <button type="button" className="btn ghost sm" onClick={() => setShow((v) => !v)}>
            {show ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      <div className="field">
        <label className="lbl">Confirm password</label>
        <input
          type={show ? "text" : "password"}
          placeholder="Repeat password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          className={pwError ? "bad" : ""}
        />
        {pwError && <small className="msg">{pwError}</small>}
      </div>

      <div className="ob-actions">
        <button className="btn" onClick={onBack} disabled={isBusy}>
          Back
        </button>
        <button
          className="btn primary"
          onClick={onImport}
          disabled={!canImport}
          data-testid="btn-import-continue"
        >
          {isBusy ? "Importing…" : "Import wallet"}
        </button>
      </div>

      <style>{css}</style>
    </section>
  );
}

const css = `
.field { display: grid; gap: 6px; margin: 12px 0; }
.lbl { font-size: 12px; opacity: 0.8; }
.mnemonic {
  width: 100%;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border, #ddd);
  background: var(--bg, #fff);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.bad { border-color: #e53935; background: #fff6f6; }
.msg { color: #b00020; }
.muted { opacity: 0.7; }
.radio-row { display: flex; gap: 12px; flex-wrap: wrap; }
.radio { display: inline-flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 999px; border: 1px solid var(--border, #ddd); }
.radio.checked { border-color: #666; }
.pw-row { display: flex; gap: 8px; }
.btn.ghost.sm { padding: 6px 8px; font-size: 12px; }
.row { display: flex; align-items: center; gap: 8px; }
.space-between { justify-content: space-between; }
`;
