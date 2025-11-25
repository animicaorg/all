(() => {
  const $ = (id) => document.getElementById(id);
  const setText = (id, v) => ($(id).textContent = v);
  const logEl = $("log");
  const log = (...args) => {
    const line = args
      .map((a) =>
        typeof a === "string" ? a : JSON.stringify(a, null, 2)
      )
      .join(" ");
    logEl.textContent += line + "\n";
    logEl.scrollTop = logEl.scrollHeight;
    console.debug("[dapp]", ...args);
  };

  const prov = window.animica;

  // Detect provider
  if (!prov || typeof prov.request !== "function") {
    setText("prov-status", "❌ no provider (load the extension?)");
    log("Provider not found. Make sure the wallet extension is installed and enabled.");
    return;
  }
  setText("prov-status", "✅ detected");
  log("Provider detected:", Object.keys(prov).filter(k => typeof prov[k] === "function" || typeof prov[k] === "object"));

  // State
  let currentAccount = null;
  let currentChainId = null;

  // Helpers that talk to provider
  async function getAccounts() {
    try {
      const accts = await prov.request({ method: "animica_requestAccounts" });
      return Array.isArray(accts) ? accts : [];
    } catch (e) {
      throw wrapErr("requestAccounts", e);
    }
  }

  async function getChainId() {
    try {
      const cid = await prov.request({ method: "animica_chainId" });
      return cid;
    } catch (e) {
      throw wrapErr("chainId", e);
    }
  }

  async function getBalance(addr) {
    try {
      // Returns integer (string or number). We just display as string.
      const bal = await prov.request({
        method: "animica_getBalance",
        params: [addr],
      });
      return typeof bal === "string" ? bal : String(bal);
    } catch (e) {
      throw wrapErr("getBalance", e);
    }
  }

  async function sendTransfer(tx) {
    try {
      // Result can be a hash or an object with {hash}
      const res = await prov.request({
        method: "animica_sendTransaction",
        params: [tx],
      });
      const hash = typeof res === "string" ? res : (res && res.hash);
      if (!hash) throw new Error("No tx hash returned");
      return hash;
    } catch (e) {
      throw wrapErr("sendTransaction", e);
    }
  }

  async function waitForReceipt(hash, { timeoutMs = 60000, pollMs = 1500 } = {}) {
    // Prefer a native wait method if provided
    try {
      const maybe = await prov.request({
        method: "animica_waitForReceipt",
        params: [hash, { timeout: timeoutMs }],
      });
      if (maybe) return maybe;
    } catch (_) {
      /* fall back to polling */
    }
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try {
        const rec = await prov.request({
          method: "animica_getTransactionReceipt",
          params: [hash],
        });
        if (rec) return rec;
      } catch (_) {
        // ignore transient
      }
      await new Promise((r) => setTimeout(r, pollMs));
    }
    throw new Error("Timed out waiting for receipt");
  }

  function wrapErr(phase, e) {
    const msg = e && (e.message || e.code || e.name) ? `${e.name || "Error"}: ${e.message || e.code}` : String(e);
    return new Error(`[${phase}] ${msg}`);
  }

  // UI updaters
  async function refreshSummary() {
    try {
      currentChainId = await getChainId();
      setText("chain", currentChainId);
    } catch (e) {
      setText("chain", "—");
      log("chainId error:", e.message);
    }
    try {
      if (currentAccount) {
        const bal = await getBalance(currentAccount);
        setText("balance", bal);
      } else {
        setText("balance", "—");
      }
    } catch (e) {
      setText("balance", "—");
      log("balance error:", e.message);
    }
  }

  function onAccountsChanged(accounts) {
    currentAccount = (accounts && accounts[0]) || null;
    setText("account", currentAccount || "—");
    log("accountsChanged:", accounts);
    // Clear dependent UI
    if (!currentAccount) {
      setText("balance", "—");
    } else {
      refreshSummary();
    }
  }

  function onChainChanged(cid) {
    currentChainId = cid;
    setText("chain", cid);
    log("chainChanged:", cid);
    refreshSummary();
  }

  function onNewHead(head) {
    // Minimal head ticker in logs
    if (head && (head.number || head.height)) {
      log("newHead:", { height: head.number ?? head.height, hash: head.hash });
    } else {
      log("newHead event");
    }
  }

  // Wire provider events
  if (typeof prov.on === "function") {
    prov.on("accountsChanged", onAccountsChanged);
    prov.on("chainChanged", onChainChanged);
    prov.on("newHeads", onNewHead);
  }

  // Connect button
  $("btn-connect").addEventListener("click", async () => {
    setText("status", "connecting…");
    try {
      const accts = await getAccounts();
      onAccountsChanged(accts);
      await refreshSummary();
      setText("status", "connected");
    } catch (e) {
      setText("status", "connect failed");
      log("connect error:", e.message);
      alert(e.message);
    }
  });

  // Send button
  $("btn-send").addEventListener("click", async () => {
    if (!currentAccount) {
      alert("Connect first");
      return;
    }
    const to = $("to").value.trim() || currentAccount;
    const amountStr = $("amount").value || "0";
    const amount = Math.max(0, Math.floor(Number(amountStr) || 0));

    const tx = {
      from: currentAccount,
      to,
      amount, // integer units (for demo)
      memo: "Demo dapp transfer",
    };

    setText("status", "sending…");
    setText("tx-hash", "—");
    setText("receipt", "—");
    log("Sending tx:", tx);

    try {
      const hash = await sendTransfer(tx);
      setText("tx-hash", hash);
      setText("status", "submitted; waiting for receipt…");
      const rec = await waitForReceipt(hash);
      setText("receipt", JSON.stringify(rec, null, 2));
      setText("status", rec.status ? "success ✅" : "failed ❌");
      log("Receipt:", rec);
      // Refresh balance after inclusion
      await refreshSummary();
    } catch (e) {
      setText("status", "send failed");
      log("send error:", e.message);
      alert(e.message);
    }
  });

  // Initial surface
  (async () => {
    try {
      // Some wallets expose selected accounts without prompting via animica_accounts
      const accts = await prov.request({ method: "animica_accounts" }).catch(() => []);
      if (Array.isArray(accts) && accts.length) {
        onAccountsChanged(accts);
      }
      await refreshSummary();
    } catch (_) {
      /* ignore */
    }
  })();
})();
