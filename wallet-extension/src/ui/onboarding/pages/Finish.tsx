import React, { useEffect, useMemo, useState } from "react";

type Props = {
  mode: "new" | "import";
  mnemonicPreview: string;
  algo: "dilithium3" | "sphincs_shake_128s";
  pin: string;
  pin2: string;
  setPin: (v: string) => void;
  setPin2: (v: string) => void;
  setAlgo: (v: "dilithium3" | "sphincs_shake_128s") => void;
  isBusy?: boolean;
  canFinish?: boolean;
  refreshToken?: number;
  primaryAddress?: string;
  onFinish: () => void;
  onBack: () => void;
  onDone?: () => void; // fired when user clicks "Finish" (window may close)
};

type NetInfo = { chainId: number; name: string };

async function bgCall<T = unknown>(message: any): Promise<T> {
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage(message, (resp: any) => {
        const err = chrome.runtime.lastError;
        if (err) return reject(new Error(err.message));
        if (resp && resp.ok) return resolve(resp.result as T);
        reject(new Error(resp?.error ?? "Background call failed"));
      });
    } catch (e: any) {
      reject(e);
    }
  });
}

function shortAddr(addr: string, n = 6) {
  if (!addr) return "";
  if (addr.length <= 2 * n) return addr;
  return `${addr.slice(0, n)}…${addr.slice(-n)}`;
}

export default function Finish({
  mode,
  mnemonicPreview,
  pin,
  pin2,
  setPin,
  setPin2,
  algo,
  setAlgo,
  isBusy = false,
  canFinish = false,
  refreshToken = 0,
  primaryAddress,
  onFinish,
  onBack,
  onDone,
}: Props) {
  const [address, setAddress] = useState<string>("");
  const [net, setNet] = useState<NetInfo | null>(null);
  const [copied, setCopied] = useState(false);
  const [show, setShow] = useState(false);
  const [addrError, setAddrError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        // Mark onboarding complete (ignore errors if bg doesn't implement)
        void bgCall({ type: "onboarding/complete" }).catch(() => {});

        const [addrRes, netRes] = await Promise.allSettled([
          bgCall<{ address: string }>({ type: "keyring/getPrimaryAddress" }),
          bgCall<NetInfo>({ type: "network/getSelected" }),
        ]);

        if (!mounted) return;

        if (addrRes.status === "fulfilled" && addrRes.value?.address) {
          setAddress(addrRes.value.address);
          setAddrError(null);
        } else if (addrRes.status === "rejected") {
          setAddrError(addrRes.reason?.message ?? "Unable to load address");
        }
        if (netRes.status === "fulfilled" && netRes.value) {
          setNet(netRes.value);
        }
      } catch {
        // best-effort; it's okay if background stubs aren't present yet
      }
    })();
    return () => {
      mounted = false;
    };
  }, [refreshToken]);

  useEffect(() => {
    if (primaryAddress) {
      setAddress(primaryAddress);
      setAddrError(null);
    }
  }, [primaryAddress]);

  useEffect(() => {
    const handler = (msg: any) => {
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "accounts/updated") {
        const next = msg.selected || msg.accounts?.[0]?.address;
        if (typeof next === "string" && next.length > 0) {
          setAddress(next);
          setAddrError(null);
        }
      }
    };

    try {
      chrome.runtime?.onMessage?.addListener(handler);
      return () => chrome.runtime?.onMessage?.removeListener(handler);
    } catch {
      return () => undefined;
    }
  }, []);

  const addrDisplay = useMemo(() => shortAddr(address), [address]);
  const mnemonicPreviewShort = useMemo(() => shortAddr(mnemonicPreview, 10), [mnemonicPreview]);

  const pinError = useMemo(() => {
    if (pin.length === 0 && pin2.length === 0) return null;
    if (pin.length < 6) return "Password must be at least 6 characters.";
    if (pin !== pin2) return "Passwords do not match.";
    return null;
  }, [pin, pin2]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // ignore
    }
  }

  function openPopup() {
    try {
      const base = window.location.origin; // chrome-extension://<id>
      window.open(`${base}/popup.html`, "_blank", "width=360,height=600");
    } catch {
      // noop
    }
  }

  function finish() {
    if (!canFinish || pinError || isBusy) return;
    onFinish();
  }

  return (
    <section className="ob-card" data-testid="finish-onboarding">
      <div className="check">✓</div>
      <h1 className="ob-title">Secure your wallet</h1>
      <p className="ob-subtitle">
        Create a password to encrypt your vault. You will use it to unlock this {mode === "new" ? "new" : "imported"} wallet.
      </p>

      <div className="field">
        <label className="lbl">Password</label>
        <div className="pw-row">
          <input
            type={show ? "text" : "password"}
            placeholder="Enter password (min 6 characters)"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
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
          value={pin2}
          onChange={(e) => setPin2(e.target.value)}
          autoComplete="new-password"
          className={pinError ? "bad" : ""}
        />
        {pinError && <small className="msg">{pinError}</small>}
      </div>

      <p className="ob-hint">
        Recovery phrase preview: <code>{mnemonicPreviewShort || "…"}</code> (store it safely — not saved by default).
      </p>

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
        <button className="btn" onClick={onBack} disabled={isBusy}>
          Back
        </button>
        <button
          className="btn primary"
          onClick={finish}
          disabled={!canFinish || !!pinError || isBusy}
          data-testid="btn-finish"
        >
          {isBusy ? "Saving…" : "Finish setup"}
        </button>
      </div>

      <h2 className="ob-title" style={{ marginTop: 18 }}>Wallet ready</h2>
      <p className="ob-subtitle">
        You’re all set{net ? ` on ${net.name} (chain ${net.chainId})` : ""}! Here’s your address:
      </p>

      <div className="addr-card">
        <code title={address}>
          {address ? addrDisplay : addrError ? "No account selected" : "Loading address…"}
        </code>
        <button className="btn ghost sm" onClick={copy} disabled={!address}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <div className="tips">
        <h3>Next steps</h3>
        <ul>
          <li>
            <strong>Pin the extension:</strong> In Chrome: puzzle icon → pin. In Firefox: toolbar
            menu → customize → drag extension to toolbar.
          </li>
          <li>
            <strong>Fund your wallet (testnet):</strong> Use the faucet in Studio or services.
          </li>
          <li>
            <strong>Connect a dapp:</strong> Sites will request permission via <code>window.animica</code>.
          </li>
        </ul>
      </div>

      <div className="ob-actions">
        <button className="btn" onClick={openPopup}>Open wallet</button>
        <button
          className="btn primary"
          onClick={() => {
            onDone?.();
            try {
              window.close();
            } catch {
              /* ignored */
            }
          }}
        >
          Close
        </button>
      </div>

      <style>{css}</style>
    </section>
  );
}

const css = `
.field { display: grid; gap: 6px; margin: 12px 0; }
.lbl { font-size: 12px; opacity: 0.8; }
.pw-row { display: flex; gap: 8px; }
.bad { border-color: #e53935; background: #fff6f6; }
.msg { color: #b00020; }
.radio-row { display: flex; gap: 12px; flex-wrap: wrap; }
.radio { display: inline-flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 999px; border: 1px solid var(--border, #ddd); }
.radio.checked { border-color: #666; }
.check {
  width: 40px; height: 40px; border-radius: 50%;
  display: grid; place-items: center;
  background: #16a34a; color: white; font-weight: 700; margin: 0 auto 12px;
}
.addr-card {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 10px 12px; margin: 10px 0 18px;
  border: 1px solid var(--border, #e1e1e1); border-radius: 10px; background: var(--bg, #fff);
}
.addr-card code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; }
.tips { text-align: left; margin: 12px 0; }
.tips h3 { margin: 6px 0 8px; font-size: 14px; }
.tips ul { margin: 0; padding-left: 18px; display: grid; gap: 6px; font-size: 13px; }
.btn.ghost.sm { padding: 6px 10px; font-size: 12px; }
`;
