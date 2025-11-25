import { useCallback, useMemo, useRef, useState } from "react";
import { drip as faucetDrip, FaucetResponse } from "../services/faucet";

/**
 * useFaucet — convenience hook around studio-services faucet endpoint.
 *
 * Depends on ../services/faucet which should export:
 *   - drip(address: string, opts?: { amount?: string | number; apiKey?: string }): Promise<FaucetResponse>
 *
 * The hook manages busy state, last tx hash, and friendly errors.
 */

export interface UseFaucet {
  /** Initiate a faucet request. Throws on failure, also updates local state. */
  request: (address: string, opts?: { amount?: string | number; apiKey?: string }) => Promise<FaucetResponse>;

  /** Whether a request is in-flight. */
  busy: boolean;

  /** Last faucet transaction hash, if service returns one. */
  lastTxHash: string | null;

  /** Last informational message (non-fatal). */
  lastMessage: string | null;

  /** Last error message (if any). */
  lastError: string | null;

  /** True if the faucet appears to be available. Set to false on 404/403/501/etc. */
  canUse: boolean;

  /** Clear local status (does not cancel in-flight). */
  reset: () => void;
}

/* --------------------------------- Helpers -------------------------------- */

function isLikelyAddress(s: string): boolean {
  // Very light heuristic: anim1… bech32m or 0x-hex fallback (dev utils)
  if (!s || typeof s !== "string") return false;
  if (s.startsWith("anim1") && s.length >= 12) return true;
  if (s.startsWith("0x") && /^[0-9a-fA-F]+$/.test(s.slice(2)) && s.length >= 10) return true;
  return false;
}

/* ----------------------------------- Hook --------------------------------- */

export function useFaucet(): UseFaucet {
  const [busyCount, setBusyCount] = useState(0);
  const [lastTxHash, setLastTxHash] = useState<string | null>(null);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [canUse, setCanUse] = useState<boolean>(true);

  const incBusy = useRef(() => setBusyCount((n) => n + 1));
  const decBusy = useRef(() => setBusyCount((n) => Math.max(0, n - 1)));
  const busy = useMemo(() => busyCount > 0, [busyCount]);

  const request: UseFaucet["request"] = useCallback(async (address, opts) => {
    setLastError(null);
    setLastMessage(null);
    setLastTxHash(null);

    if (!isLikelyAddress(address)) {
      const msg = "Invalid address format";
      setLastError(msg);
      throw new Error(msg);
    }

    incBusy.current();
    setLastMessage("Requesting faucet drip…");
    try {
      const res = await faucetDrip(address, { amount: opts?.amount, apiKey: opts?.apiKey });

      if (res.txHash) {
        setLastTxHash(res.txHash);
        setLastMessage(`Faucet sent ${res.amount ?? "funds"} — tx ${res.txHash.slice(0, 10)}…`);
      } else {
        setLastMessage(`Faucet responded — credited ${res.amount ?? "funds"}.`);
      }

      // If service explicitly signals disabled, propagate to state.
      if ((res as any).disabled === true) {
        setCanUse(false);
      } else {
        setCanUse(true);
      }

      return res;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      setLastError(msg);

      // Heuristics: recognize common "faucet off" statuses surfaced as errors by services layer.
      const code = (e && (e.status || e.code)) ?? 0;
      if (code === 404 || code === 403 || code === 501) {
        setCanUse(false);
      }

      throw e;
    } finally {
      decBusy.current();
    }
  }, []);

  const reset = useCallback(() => {
    setBusyCount(0);
    setLastTxHash(null);
    setLastMessage(null);
    setLastError(null);
    // Do not toggle canUse here; preserve knowledge about availability.
  }, []);

  return {
    request,
    busy,
    lastTxHash,
    lastMessage,
    lastError,
    canUse,
    reset,
  };
}

export default useFaucet;

/* --------------------------------- Types ---------------------------------- */
/**
 * FaucetResponse shape is re-exported from services/faucet. Included here for
 * reference only:
 *
 * export interface FaucetResponse {
 *   address: string;
 *   amount: string;          // human string or base units, as returned by server
 *   unit?: string;           // optional (e.g., "ANM")
 *   txHash?: string;         // optional tx hash if drip performed on-chain
 *   message?: string;        // optional human message
 *   disabled?: boolean;      // optional flag when faucet is turned off server-side
 * }
 */
