import React, { useEffect, useMemo, useRef, useState } from "react";
import NetworkSelect from "../components/NetworkSelect";
import AccountSelect from "../components/AccountSelect";
import BalanceCard from "../components/BalanceCard";

type EstimateResult = {
  gasUsed: number;
  fee: string; // human-readable (e.g., "0.00042 ANM")
  canAfford?: boolean;
  warnings?: string[];
};

type SelectedState = {
  address: string;
  chainId: number;
  networkName?: string;
  symbol?: string; // e.g., ANM
  decimals?: number; // e.g., 18
};

type BalanceInfo = {
  free: string; // human formatted (e.g., "12.34")
  symbol: string;
  decimals: number;
};

function callBackground<T = any>(msg: any): Promise<T> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp) => resolve(resp as T));
    } catch {
      resolve(undefined as unknown as T);
    }
  });
}

function classNames(...xs: Array<string | undefined | false>) {
  return xs.filter(Boolean).join(" ");
}

export default function Send() {
  const [selected, setSelected] = useState<SelectedState | null>(null);
  const [balance, setBalance] = useState<BalanceInfo | null>(null);

  const [to, setTo] = useState("");
  const [amount, setAmount] = useState(""); // decimal string
  const [memo, setMemo] = useState(""); // optional hex or text
  const [priority, setPriority] = useState<"low" | "normal" | "high">("normal");

  const [est, setEst] = useState<EstimateResult | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [txHash, setTxHash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const debounceId = useRef<number | null>(null);

  // Load selected account/network and balance
  const refreshSelected = async () => {
    const s = await callBackground<SelectedState>({ type: "wallet.getSelected" });
    if (s && s.address) setSelected(s);

    if (s?.address) {
      const b = await callBackground<BalanceInfo>({
        type: "wallet.getBalance",
        address: s.address,
      });
      if (b && b.symbol) setBalance(b);
    }
  };

  useEffect(() => {
    void refreshSelected();
  }, []);

  // Basic, UI-level address check (background does authoritative validation)
  const addrInvalid = useMemo(() => {
    if (!to) return false;
    const isBech = /^anim1[0-9a-z]{6,}$/i.test(to);
    const isHex = /^0x[0-9a-f]{40}$/i.test(to); // allow dev hex for early nets
    return !(isBech || isHex);
  }, [to]);

  const amountInvalid = useMemo(() => {
    if (!amount) return false;
    if (!/^\d+(\.\d+)?$/.test(amount)) return true;
    return false;
  }, [amount]);

  // Debounced estimate when inputs change
  const triggerEstimate = () => {
    if (debounceId.current) {
      window.clearTimeout(debounceId.current);
    }
    debounceId.current = window.setTimeout(() => void estimate(), 450);
  };

  useEffect(() => {
    if (to && amount && !addrInvalid && !amountInvalid) {
      triggerEstimate();
    } else {
      setEst(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [to, amount, priority, selected?.chainId]);

  const estimate = async () => {
    if (!selected?.address) return;
    if (!to || !amount || addrInvalid || amountInvalid) return;

    setEstimating(true);
    setError(null);
    try {
      const res = await callBackground<EstimateResult>({
        type: "tx.simulateTransfer",
        from: selected.address,
        to,
        amount, // decimal string; background converts to base units
        memo: memo || undefined,
        priority, // hint to fee/tip policy if supported
      });
      setEst(res || null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to estimate fee.");
      setEst(null);
    } finally {
      setEstimating(false);
    }
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected?.address) return;
    if (addrInvalid || amountInvalid) return;

    setSubmitting(true);
    setError(null);
    setTxHash(null);
    try {
      // Background may open the Approval window (MV3) and block until user approves/rejects.
      const resp = await callBackground<{ txHash?: string; error?: string }>({
        type: "tx.submitTransfer",
        from: selected.address,
        to,
        amount,
        memo: memo || undefined,
        priority,
      });

      if (resp?.error) {
        setError(resp.error);
      } else if (resp?.txHash) {
        setTxHash(resp.txHash);
        // Refresh balance after a short delay (mempool)
        setTimeout(() => void refreshSelected(), 800);
      } else {
        setError("Transaction was not submitted.");
      }
    } catch (e: any) {
      setError(e?.message ?? "Failed to submit transaction.");
    } finally {
      setSubmitting(false);
    }
  };

  const canSend =
    !!selected?.address &&
    !!to &&
    !!amount &&
    !addrInvalid &&
    !amountInvalid &&
    !estimating &&
    !submitting;

  return (
    <section className="ami-send">
      <div className="ami-section-header">
        <h2 className="ami-section-title">Send</h2>
      </div>

      <div className="ami-row">
        <NetworkSelect />
      </div>
      <div className="ami-row">
        <AccountSelect />
      </div>
      <div className="ami-row">
        <BalanceCard
          balance={balance?.free ?? "--"}
          symbol={balance?.symbol ?? selected?.symbol ?? "ANM"}
          onRefresh={() => refreshSelected()}
        />
      </div>

      <form className="ami-form" onSubmit={onSubmit} autoComplete="off">
        <label className="ami-label">Recipient</label>
        <input
          className={classNames("ami-input", addrInvalid && "ami-input-error")}
          placeholder="anim1… or 0x… (dev)"
          value={to}
          onChange={(e) => setTo(e.target.value.trim())}
          spellCheck={false}
          inputMode="latin"
        />
        {addrInvalid && <div className="ami-help-error">Address format looks invalid.</div>}

        <div className="ami-grid-2">
          <div>
            <label className="ami-label">Amount</label>
            <input
              className={classNames("ami-input", amountInvalid && "ami-input-error")}
              placeholder="0.0"
              value={amount}
              onChange={(e) => setAmount(e.target.value.trim())}
              inputMode="decimal"
            />
            {amountInvalid && <div className="ami-help-error">Enter a valid number.</div>}
          </div>

          <div>
            <label className="ami-label">Priority</label>
            <select
              className="ami-select"
              value={priority}
              onChange={(e) => setPriority(e.target.value as any)}
            >
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
            </select>
          </div>
        </div>

        <label className="ami-label">Memo (optional)</label>
        <input
          className="ami-input"
          placeholder="Note for recipient (stored in logs)"
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          maxLength={120}
        />

        <div className="ami-estimate">
          <button
            type="button"
            className="ami-btn ami-btn-secondary"
            onClick={() => estimate()}
            disabled={estimating || !to || !amount || addrInvalid || amountInvalid}
            aria-busy={estimating}
          >
            {estimating ? "Estimating…" : "Estimate fee"}
          </button>

          <div className="ami-estimate-info">
            {est ? (
              <>
                <div className="ami-kv">
                  <span>Estimated gas</span>
                  <span>{est.gasUsed.toLocaleString()}</span>
                </div>
                <div className="ami-kv">
                  <span>Estimated fee</span>
                  <span>{est.fee}</span>
                </div>
                {est.warnings?.length ? (
                  <ul className="ami-warnings">
                    {est.warnings.map((w, i) => (
                      <li key={i}>⚠️ {w}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : (
              <div className="ami-help">Enter recipient & amount to estimate fees.</div>
            )}
          </div>
        </div>

        {error && <div className="ami-alert ami-alert-error">{error}</div>}
        {txHash && (
          <div className="ami-alert ami-alert-success">
            Sent! Tx hash:
            <code className="ami-code" title={txHash}>
              {txHash.slice(0, 10)}…{txHash.slice(-8)}
            </code>
          </div>
        )}

        <div className="ami-actions">
          <button type="submit" className="ami-btn ami-btn-primary" disabled={!canSend} aria-busy={submitting}>
            {submitting ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
