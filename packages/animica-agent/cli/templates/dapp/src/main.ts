// Animica dapp seed. Replace this with your application logic.
const status = document.getElementById("status") as HTMLPreElement | null;

async function probe(): Promise<void> {
  if (!status) return;
  try {
    const res = await fetch("http://127.0.0.1:8545/rpc", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "animica_chainId", params: [] }),
    });
    const j = await res.json();
    status.textContent = JSON.stringify(j, null, 2);
  } catch (err) {
    status.textContent = String(err);
  }
}

probe();
