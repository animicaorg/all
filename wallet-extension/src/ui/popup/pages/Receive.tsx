import React, { useEffect, useMemo, useState } from "react";
import NetworkSelect from "../components/NetworkSelect";
import AccountSelect from "../components/AccountSelect";
import BalanceCard from "../components/BalanceCard";

type SelectedState = {
  address: string;       // bech32m (anim1…)
  chainId: number;
  networkName?: string;
  symbol?: string;       // e.g., ANM
  decimals?: number;     // e.g., 18
};

type BalanceInfo = {
  free: string;          // human formatted (e.g., "12.34")
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

export default function Receive() {
  const [selected, setSelected] = useState<SelectedState | null>(null);
  const [balance, setBalance] = useState<BalanceInfo | null>(null);

  // Optional request params to generate a payment URI
  const [reqAmount, setReqAmount] = useState<string>("");
  const [memo, setMemo] = useState<string>("");

  const [copied, setCopied] = useState<"addr" | "uri" | null>(null);

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

  const amountInvalid = useMemo(() => {
    if (!reqAmount) return false;
    if (!/^\d+(\.\d+)?$/.test(reqAmount)) return true;
    return false;
  }, [reqAmount]);

  // Create a simple payment URI (non-normative; handy for QR/links)
  const paymentUri = useMemo(() => {
    if (!selected?.address) return "";
    const params = new URLSearchParams();
    if (reqAmount && !amountInvalid) params.set("amount", reqAmount);
    if (memo) params.set("memo", memo);
    const q = params.toString();
    return q ? `animica:${selected.address}?${q}` : `animica:${selected.address}`;
  }, [selected?.address, reqAmount, amountInvalid, memo]);

  const copy = async (text: string, kind: "addr" | "uri") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      // ignore
    }
  };

  const canShare = typeof navigator !== "undefined" && !!(navigator as any).share;

  const sharePayment = async () => {
    if (!paymentUri) return;
    try {
      if (canShare) {
        await (navigator as any).share({
          title: "Animica address",
          text: memo ? memo : "Send on Animica",
          url: paymentUri,
        });
      } else {
        await copy(paymentUri, "uri");
      }
    } catch {
      // user canceled
    }
  };

  return (
    <section className="ami-receive">
      <div className="ami-section-header">
        <h2 className="ami-section-title">Receive</h2>
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

      <div className="ami-card ami-card-tight">
        <label className="ami-label">Your address</label>
        <div className="ami-address-box">
          <code className="ami-code ami-code-wrap" title={selected?.address || ""}>
            {selected?.address || "—"}
          </code>
          <div className="ami-actions-inline">
            <button
              className="ami-btn ami-btn-secondary"
              onClick={() => selected?.address && copy(selected.address, "addr")}
              disabled={!selected?.address}
            >
              {copied === "addr" ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>

        <div className="ami-hint">
          Only receive on <strong>{selected?.networkName ?? "this network"}</strong> (chainId{" "}
          <code className="ami-code">{selected?.chainId ?? "?"}</code>). Sending from other networks
          may result in loss of funds.
        </div>
      </div>

      <form className="ami-form ami-card" onSubmit={(e) => e.preventDefault()}>
        <div className="ami-grid-2">
          <div>
            <label className="ami-label">Request amount (optional)</label>
            <input
              className={classNames("ami-input", amountInvalid && "ami-input-error")}
              placeholder="0.0"
              value={reqAmount}
              onChange={(e) => setReqAmount(e.target.value.trim())}
              inputMode="decimal"
            />
            {amountInvalid && <div className="ami-help-error">Enter a valid number.</div>}
          </div>
          <div>
            <label className="ami-label">Memo (optional)</label>
            <input
              className="ami-input"
              placeholder="What is this for?"
              value={memo}
              onChange={(e) => setMemo(e.target.value)}
              maxLength={120}
            />
          </div>
        </div>

        <label className="ami-label">Payment link</label>
        <div className="ami-address-box">
          <code className="ami-code ami-code-wrap" title={paymentUri}>
            {paymentUri || "—"}
          </code>
          <div className="ami-actions-inline">
            <button
              className="ami-btn ami-btn-secondary"
              onClick={() => paymentUri && copy(paymentUri, "uri")}
              disabled={!paymentUri}
            >
              {copied === "uri" ? "Copied!" : "Copy link"}
            </button>
            <button className="ami-btn ami-btn-secondary" onClick={sharePayment} disabled={!paymentUri}>
              {canShare ? "Share…" : "Copy"}
            </button>
          </div>
        </div>

        <div className="ami-hint">
          You can turn the link into a QR code in any QR app. The wallet will parse <code className="ami-code">amount</code> and{" "}
          <code className="ami-code">memo</code> if present.
        </div>
      </form>

      <div className="ami-card ami-card-muted">
        <ul className="ami-list">
          <li>Never share your seed phrase or vault password.</li>
          <li>For testnets, faucet funds may be subject to rate limits.</li>
          <li>Large deposits? Send a small test first, then the remainder.</li>
        </ul>
      </div>
    </section>
  );
}
