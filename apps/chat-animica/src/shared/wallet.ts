export type AnimicaProvider = {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
};

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

function provider() {
  return typeof window !== "undefined" ? window.animica : undefined;
}

export async function detectWallet() {
  const p = provider();
  if (!p) return { installed: false };
  const chainId = await p.request({ method: "animica_chainId" });
  return { installed: true, chainId: String(chainId) };
}

export async function requestAccounts() {
  const p = provider();
  if (!p) throw new Error("Animica wallet not installed");
  return p.request({ method: "animica_requestAccounts" });
}

export async function switchChain(chainId: string) {
  const p = provider();
  if (!p) throw new Error("Animica wallet not installed");
  return p.request({ method: "animica_switchChain", params: [chainId] });
}
