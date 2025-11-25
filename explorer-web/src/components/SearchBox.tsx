import React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

type Props = {
  className?: string;
  autoFocus?: boolean;
  placeholder?: string;
};

/**
 * Heuristic classifiers for quick search:
 * - Height:           decimal digits (e.g. "12345")      → /blocks/:height
 * - Hash (32 bytes):  64 hex chars with/without "0x"     → /tx/:hash (default)
 * - Address:          bech32-ish (hrp1data...) or 0x40   → /address/:addr
 *
 * Note: We intentionally avoid network calls here to keep UX snappy and avoid coupling
 * to specific RPC method names. If a block-hash is entered, most explorers still land
 * on the tx page gracefully; you can augment routing later to try both.
 */
function normalizeHex(s: string): string {
  const x = s.startsWith("0x") ? s : `0x${s}`;
  return x.toLowerCase();
}

const RE_HEIGHT = /^[0-9]{1,18}$/;
// 32 bytes hex (sha256/keccak-ish)
const RE_HASH64 = /^(0x)?[0-9a-fA-F]{64}$/;
// EVM-ish address fallback (20 bytes)
const RE_ADDR_HEX40 = /^(0x)?[0-9a-fA-F]{40}$/;
// Very permissive bech32-like (no 1, B32 charset after separator)
const RE_ADDR_BECH32ISH = /^[a-z0-9]{1,83}1[ac-hj-np-z02-9]{6,}$/;

type Target =
  | { kind: "height"; to: string }
  | { kind: "hash"; to: string }
  | { kind: "address"; to: string }
  | { kind: "unknown"; reason: string };

function classify(inputRaw: string): Target {
  const s = inputRaw.trim();
  if (!s) return { kind: "unknown", reason: "empty" };

  if (RE_HEIGHT.test(s)) {
    // Block height
    return { kind: "height", to: `/blocks/${s}` };
  }

  if (RE_HASH64.test(s)) {
    // Assume transaction hash by default (can be refined later to try block first)
    const h = normalizeHex(s);
    return { kind: "hash", to: `/tx/${h}` };
  }

  if (RE_ADDR_BECH32ISH.test(s)) {
    return { kind: "address", to: `/address/${s}` };
  }

  if (RE_ADDR_HEX40.test(s)) {
    return { kind: "address", to: `/address/${normalizeHex(s)}` };
  }

  return { kind: "unknown", reason: "unrecognized" };
}

export default function SearchBox({ className, autoFocus, placeholder }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const onSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const target = classify(q);
    if (target.kind === "unknown") {
      setError(
        t(
          "searchBox.unrecognized",
          "Enter a block height, hash (0x…), or address (bech32/0x…)."
        )
      );
      return;
    }
    setError(null);
    navigate(target.to);
    // Optionally keep the query for back nav; otherwise clear:
    // setQ("");
  };

  // Handy keyboard shortcuts: "/" to focus, "Escape" to clear/blur.
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "/" && !/input|textarea/i.test((ev.target as any)?.tagName)) {
        ev.preventDefault();
        inputRef.current?.focus();
      } else if (ev.key === "Escape" && document.activeElement === inputRef.current) {
        setQ("");
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ph =
    placeholder ??
    t("searchBox.placeholder", "Search by hash / height / address…");

  return (
    <form className={["search-box", className].filter(Boolean).join(" ")} onSubmit={onSubmit} role="search" aria-label={t("searchBox.ariaLabel", "Quick search")}>
      <div className="field">
        <span className="icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24">
            <path
              fill="currentColor"
              d="M10 2a8 8 0 1 1 5.293 13.707l4 4-1.414 1.414-4-4A8 8 0 0 1 10 2zm0 2a6 6 0 1 0 0 12A6 6 0 0 0 10 4z"
            />
          </svg>
        </span>
        <input
          ref={inputRef}
          autoFocus={autoFocus}
          type="text"
          inputMode="search"
          spellCheck={false}
          autoComplete="off"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmit();
          }}
          placeholder={ph}
          aria-invalid={!!error}
          aria-describedby="searchbox-help"
        />
        {q && (
          <button
            type="button"
            className="clear"
            aria-label={t("searchBox.clear", "Clear")}
            onClick={() => {
              setQ("");
              setError(null);
              inputRef.current?.focus();
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="currentColor" d="M18.3 5.71L12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.3 19.71 2.89 18.3 9.18 12 2.89 5.71 4.3 4.29 10.59 10.6l6.3-6.3z" />
            </svg>
          </button>
        )}
        <button type="submit" className="go" aria-label={t("searchBox.go", "Go")}>
          {t("searchBox.goShort", "Go")}
        </button>
      </div>

      <div id="searchbox-help" className="help">
        {error ? (
          <span className="error">{error}</span>
        ) : (
          <span>
            <kbd>/</kbd> {t("searchBox.focus", "focus")} · <kbd>Enter</kbd>{" "}
            {t("searchBox.toSearch", "to search")} · <kbd>Esc</kbd>{" "}
            {t("searchBox.clearBlur", "to clear")}
          </span>
        )}
      </div>

      <style>{css}</style>
    </form>
  );
}

const css = `
.search-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 220px;
}

.search-box .field{
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 6px 6px 30px;
  border: 1px solid var(--border-muted, #e5e7eb);
  border-radius: 8px;
  background: var(--bg-elev-0, #fff);
}

.search-box .icon{
  position: absolute;
  left: 8px;
  color: var(--text-muted, #6b7280);
  display: inline-flex;
}

.search-box input{
  flex: 1;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: var(--text, #111827);
  min-width: 0;
}

.search-box input::placeholder{
  color: var(--text-muted, #9ca3af);
}

.search-box .clear{
  border: 0;
  background: transparent;
  color: var(--text-muted, #6b7280);
  padding: 2px;
  border-radius: 6px;
  cursor: pointer;
}

.search-box .clear:hover{
  background: var(--bg-elev-1, #f3f4f6);
}

.search-box .go{
  border: 0;
  background: var(--brand, #111827);
  color: white;
  font-weight: 600;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
}

.search-box .go:hover{
  filter: brightness(1.05);
}

.search-box .help{
  font-size: 12px;
  color: var(--text-muted, #6b7280);
}

.search-box .help .error{
  color: #ef4444;
}

.search-box kbd{
  background: var(--bg-elev-1, #f3f4f6);
  border: 1px solid var(--border-muted, #e5e7eb);
  border-bottom-width: 2px;
  border-radius: 6px;
  padding: 0 4px;
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}
`;
