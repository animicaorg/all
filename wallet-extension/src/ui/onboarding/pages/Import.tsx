import React, { useMemo, useState } from "react";

type Props = {
  onBack: () => void;
  /**
   * Called when the mnemonic is validated so the parent can proceed to the
   * password step. The parent handles persistence to avoid duplicate imports.
   */
  onSubmit: (mnemonic: string, algo: Algo) => void;
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

export default function Import({ onBack, onSubmit }: Props) {
  const [mnemonicText, setMnemonicText] = useState("");
  const [algo, setAlgo] = useState<Algo>("dilithium3");
  const words = useMemo(() => normWords(mnemonicText), [mnemonicText]);
  const mnemonicError = useMemo(() => validateMnemonic(words), [words]);
  const canContinue = !mnemonicError;

  function onImport() {
    onSubmit(words.join(" "), algo);
  }

  return (
    <section className="ob-card" data-testid="import-mnemonic">
      <h1 className="ob-title">Import wallet</h1>
      <p className="ob-subtitle">Paste your recovery phrase and choose a signature algorithm to continue.</p>

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

      <div className="ob-actions">
        <button className="btn" onClick={onBack}>
          Back
        </button>
        <button
          className="btn primary"
          onClick={onImport}
          disabled={!canContinue}
          data-testid="btn-import-continue"
        >
          Continue
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
