import React, { useMemo, useState, useEffect, useCallback } from "react";

type Props = {
  mnemonic: string;
  isBusy?: boolean;
  onBack: () => void;
  onNext: () => void; // fired once verification succeeds
};

/**
 * Deterministic selection of K distinct indices in [1..N] using a tiny xorshift PRNG
 * seeded from a stable 32-bit hash of the mnemonic string.
 */
function pickIndices(n: number, k: number, seedStr: string): number[] {
  const seed = djb2_32(seedStr);
  let x = seed >>> 0;
  const out: number[] = [];
  const seen = new Set<number>();
  const tries = Math.max(16, k * 8);
  let guard = 0;

  while (out.length < k && guard++ < tries) {
    // xorshift32
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    const idx = (x >>> 0) % n; // 0..n-1
    const oneBased = idx + 1;
    if (!seen.has(oneBased)) {
      seen.add(oneBased);
      out.push(oneBased);
    }
  }

  // Fallback to fill if collisions starved us (rare)
  for (let i = 1; out.length < k && i <= n; i++) {
    if (!seen.has(i)) out.push(i);
  }
  return out;
}

function djb2_32(s: string): number {
  let h = 5381 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h = (((h << 5) + h) ^ s.charCodeAt(i)) >>> 0; // h*33 ^ c
  }
  return h >>> 0;
}

function norm(w: string): string {
  return w.trim().toLowerCase().replace(/[^a-z]/g, "");
}

export default function VerifyMnemonic({ mnemonic, isBusy = false, onBack, onNext }: Props) {
  const words = useMemo(() => mnemonic.trim().split(/\s+/).map(norm).filter(Boolean), [mnemonic]);
  const targets = useMemo(() => pickIndices(words.length, Math.min(3, words.length), mnemonic), [words.length, mnemonic]);

  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [touched, setTouched] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [showHint, setShowHint] = useState<boolean>(false);
  const [ok, setOk] = useState<boolean>(false);

  useEffect(() => {
    setAnswers({});
    setTouched({});
    setError(null);
    setOk(false);
  }, [mnemonic]);

  const validate = useCallback(() => {
    for (const pos of targets) {
      const want = words[pos - 1];
      const got = norm(answers[pos] ?? "");
      if (!got || got !== want) return false;
    }
    return true;
  }, [answers, targets, words]);

  useEffect(() => {
    setOk(validate());
  }, [answers, validate]);

  const onInput = (pos: number, v: string) => {
    setAnswers((a) => ({ ...a, [pos]: v }));
  };

  const onBlur = (pos: number) => {
    setTouched((t) => ({ ...t, [pos]: true }));
  };

  const handleSubmit = () => {
    if (ok) {
      onNext();
    } else {
      setError("One or more words are incorrect. Please check and try again.");
    }
  };

  const onKeyDown: React.KeyboardEventHandler<HTMLFormElement> = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <section className="ob-card" data-testid="verify-mnemonic">
      <h1 className="ob-title">Verify your recovery phrase</h1>
      <p className="ob-subtitle">
        To make sure you wrote it down correctly, enter the words for the requested positions.
      </p>

      {error && (
        <p className="ob-warning" role="alert">
          {error}
        </p>
      )}

      <form className="verify-grid" onKeyDown={onKeyDown}>
        {targets.map((pos) => {
          const want = words[pos - 1];
          const val = answers[pos] ?? "";
          const bad = touched[pos] && norm(val) !== "" && norm(val) !== want;
          return (
            <label key={pos} className={"verify-field" + (bad ? " bad" : "")}>
              <span className="lbl">Word #{pos}</span>
              <input
                type="text"
                inputMode="text"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="enter word"
                value={val}
                onChange={(e) => onInput(pos, e.target.value)}
                onBlur={() => onBlur(pos)}
                data-testid={`word-${pos}`}
              />
              {bad && <span className="msg">Doesn’t match — check spelling/case.</span>}
            </label>
          );
        })}
      </form>

      <div className="ob-actions row">
        <button className="btn" onClick={() => setShowHint((v) => !v)}>
          {showHint ? "Hide hint" : "Show hint"}
        </button>
      </div>

      {showHint && (
        <div className="hint-box">
          <p className="hint-title">Hint</p>
          <ol className="mnemonic-grid small">
            {words.map((w, i) => (
              <li key={i}>
                <span className="idx">{i + 1}</span>
                <span className="word">{w}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="ob-actions">
        <button className="btn" onClick={onBack} disabled={isBusy}>
          Back
        </button>
        <button
          className="btn primary"
          onClick={handleSubmit}
          disabled={!ok || isBusy}
          data-testid="btn-verify-continue"
        >
          Continue
        </button>
      </div>

      <style>{css}</style>
    </section>
  );
}

const css = `
.verify-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  margin: 12px 0;
}
.verify-field {
  display: grid;
  gap: 6px;
}
.verify-field .lbl {
  font-size: 12px;
  opacity: 0.8;
}
.verify-field input {
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border, #ddd);
  background: var(--bg, #fff);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.verify-field.bad input {
  border-color: #e53935;
  background: #fff6f6;
}
.verify-field .msg {
  color: #b00020;
  font-size: 12px;
}
.hint-box {
  margin: 8px 0 0;
  padding: 10px 12px;
  border: 1px dashed var(--border, #ddd);
  border-radius: 8px;
  background: var(--bgElev, #fafafa);
}
.hint-title {
  margin: 0 0 6px;
  font-size: 12px;
  opacity: 0.8;
}
.mnemonic-grid.small {
  columns: 2;
  column-gap: 16px;
  list-style: none;
  padding: 0;
  margin: 0;
}
.mnemonic-grid.small li {
  break-inside: avoid;
  display: flex;
  gap: 8px;
  padding: 2px 0;
}
.mnemonic-grid .idx { width: 20px; opacity: 0.6; text-align: right; }
.mnemonic-grid .word { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
`;
