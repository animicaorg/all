// Lightweight no-build dashboard. Polls /api/status every 4s and renders
// per-tab views. Designed for cold-start clarity over visual flair.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function setMark(el, ok, label) {
  el.textContent = label;
  el.classList.remove("good", "warn", "bad");
  el.classList.add(ok === true ? "good" : ok === false ? "bad" : "warn");
}

function bn(v) {
  if (v === undefined || v === null) return null;
  if (typeof v === "object" && "__bn" in v) return String(v.__bn);
  return String(v);
}

function setConn(ok, text) {
  $("#conn-dot").classList.remove("good", "bad");
  $("#conn-dot").classList.add(ok ? "good" : "bad");
  $("#conn-label").textContent = text;
}

// Tab switching.
$$(".nav-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".nav-btn").forEach((b) => b.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  }),
);

// Copy button.
$("#copy-address").addEventListener("click", async () => {
  const text = $("#wallet-address").textContent.trim();
  if (text && text !== "—") {
    try {
      await navigator.clipboard.writeText(text);
      $("#copy-address").textContent = "Copied!";
      setTimeout(() => ($("#copy-address").textContent = "Copy"), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }
});
$("#refresh-balance").addEventListener("click", () => refresh());

async function refresh() {
  let s;
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    s = await r.json();
    setConn(true, "connected");
  } catch (err) {
    setConn(false, "offline");
    return;
  }

  // Agent / readiness tab.
  const rpcOk = !!s.node?.reachable;
  const chainOk = bn(s.node?.chainId) === String(s.config.chainId);
  const walletAddr = s.wallet?.address;
  const balanceRaw = bn(s.wallet?.raw);
  const funded = balanceRaw && BigInt(balanceRaw) > 0n;

  setMark($('[data-key="rpc"] span'), rpcOk, rpcOk ? "yes" : "no");
  setMark($('[data-key="chain"] span'), chainOk, chainOk ? "yes" : "no");
  setMark($('[data-key="wallet"] span'), !!walletAddr, walletAddr ? "yes" : "no");
  setMark($('[data-key="balance"] span'), funded === true, funded ? "yes" : balanceRaw ? "no" : "—");
  const mode = s.config.settlementMode ?? "offline";
  setMark($('[data-key="settlement"] span'), mode !== "live", mode);

  // Next steps suggestions.
  const steps = [];
  if (!rpcOk) steps.push("Start the local node: animica-agent node start");
  if (rpcOk && !chainOk) steps.push("Configured chainId does not match the node. Adjust .animica/agent.json.");
  if (!walletAddr) steps.push("Create a wallet: animica-agent wallet create main");
  if (walletAddr && !funded) steps.push("Fund your wallet — see the Wallet tab for the address.");
  if (rpcOk && walletAddr && funded) steps.push("All systems go. Run agent tasks: animica-agent code \"<task>\"");
  const ol = $("#next-steps");
  ol.innerHTML = "";
  steps.forEach((t) => {
    const li = document.createElement("li");
    li.textContent = t;
    ol.appendChild(li);
  });

  // Wallet tab.
  $("#wallet-address").textContent = walletAddr ?? "—";
  $("#wallet-source").textContent = `source: ${s.wallet?.source ?? "—"}`;
  $("#balance-amt").textContent = s.wallet?.formattedANM ?? "—";
  $("#balance-raw").textContent = balanceRaw ? `raw: ${balanceRaw}` : "raw: —";
  $("#funding-card").hidden = !!funded;

  // Node tab.
  $("#node-url").textContent = s.config.rpcUrl;
  $("#node-chain").textContent = bn(s.node?.chainId) ?? "—";
  $("#node-block").textContent = bn(s.node?.blockNumber) ?? "—";
  $("#node-client").textContent = s.node?.clientVersion ?? "—";
  $("#node-reachable").textContent = rpcOk ? "yes" : "no";

  // Useful work tab.
  $("#uw-worker").textContent = s.miner?.identity?.worker ?? "—";
  $("#uw-payout").textContent = s.miner?.identity?.payoutAddress ?? "—";
  $("#uw-hashrate").textContent = s.miner?.live?.hashrate ?? "—";
  $("#uw-metrics").textContent = s.miner?.live?.metricsReachable ? "reachable" : "not reachable";

  // Settings tab — show config (BigInt-safe).
  $("#settings-config").textContent = JSON.stringify(s.config, null, 2);

  // Explorer link.
  const link = document.getElementById("open-explorer");
  if (link && s.config.explorerUrl) link.href = s.config.explorerUrl;
  $("#agent-subtitle").textContent = funded && rpcOk
    ? "Ready — wallet funded and node healthy."
    : "Setup is in progress; complete the checklist below.";
}

refresh();
setInterval(refresh, 4000);
