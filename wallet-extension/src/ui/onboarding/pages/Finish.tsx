import React, { useEffect, useMemo, useState } from "react";

type Props = {
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

export default function Finish({ onDone }: Props) {
  const [address, setAddress] = useState<string>("");
  const [net, setNet] = useState<NetInfo | null>(null);
  const [copied, setCopied] = useState(false);

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
  }, []);

  const addrDisplay = useMemo(() => shortAddr(address), [address]);

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
    onDone?.();
    // Close the onboarding window if it was opened as a separate window
    try {
      window.close();
    } catch {
      /* ignored */
    }
  }

  return (
    <section className="ob-card" data-testid="finish-onboarding">
      <div className="check">✓</div>
      <h1 className="ob-title">Wallet ready</h1>
      <p className="ob-subtitle">
        You’re all set{net ? ` on ${net.name} (chain ${net.chainId})` : ""}! Here’s your address:
      </p>

      <div className="addr-card">
        <code title={address}>{addrDisplay || "anim1…"}</code>
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
        <button className="btn primary" onClick={finish} data-testid="btn-finish">
          Finish
        </button>
      </div>

      <style>{css}</style>
    </section>
  );
}

const css = `
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
