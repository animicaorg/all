/**
 * Browser-side wallet wrapper for the Animica browser-extension provider
 * (window.animica). Mirrors the surface that studio.animica.org uses so a
 * user with the extension installed can deposit ANIMICA into the academy
 * reward pool, sign attestations, and receive payouts without ever pasting
 * a private key into this site.
 *
 * The provider object is injected by the extension at document_idle. This
 * helper polls briefly on first call so a tutorial step that opens with a
 * "connect wallet" prompt does not race extension injection.
 */

export interface WalletState {
  address: string | null;
  network: string | null;
  connected: boolean;
}

const PROVIDER_POLL_INTERVAL_MS = 100;
const PROVIDER_POLL_TIMEOUT_MS = 2_000;

export async function getProvider(): Promise<AnimicaWalletProvider> {
  if (typeof window === "undefined") {
    throw new Error("wallet provider only available in the browser");
  }
  if (window.animica) return window.animica;

  const deadline = Date.now() + PROVIDER_POLL_TIMEOUT_MS;
  return new Promise<AnimicaWalletProvider>((resolve, reject) => {
    const tick = () => {
      if (window.animica) {
        resolve(window.animica);
        return;
      }
      if (Date.now() >= deadline) {
        reject(
          new Error(
            "Animica wallet extension not detected. Install it from animica.org/wallet and reload.",
          ),
        );
        return;
      }
      setTimeout(tick, PROVIDER_POLL_INTERVAL_MS);
    };
    tick();
  });
}

export async function connectWallet(): Promise<{
  address: string;
  network: string;
  connected: true;
}> {
  const provider = await getProvider();
  const { address, network } = await provider.connect();
  return { address, network, connected: true };
}

export async function readWallet(): Promise<WalletState> {
  if (typeof window === "undefined" || !window.animica) {
    return { address: null, network: null, connected: false };
  }
  const address = await window.animica.getAddress().catch(() => null);
  return { address, network: null, connected: address !== null };
}

/**
 * Signs an attestation that the user completed a tutorial step. The signed
 * payload lets the academy backend prove the click came from the wallet
 * owner without requiring an on-chain tx per step (cheap + UX-friendly).
 */
export async function signCompletionAttestation(req: {
  tutorialId: string;
  stepId: string;
  address: string;
  nonce: string;
}): Promise<{ signature: string; message: string }> {
  const provider = await getProvider();
  const message = [
    "Animica Academy completion attestation",
    `tutorial: ${req.tutorialId}`,
    `step: ${req.stepId}`,
    `address: ${req.address}`,
    `nonce: ${req.nonce}`,
  ].join("\n");
  const signature = await provider.signMessage(message);
  return { signature, message };
}

/**
 * Deposits ANIMICA into the academy reward pool. The pool is an on-chain
 * address whose balance funds tutorial payouts. The extension renders the
 * confirmation UI with the destination + amount visible — we never bypass
 * that surface.
 *
 *   amountAnim  — float string of whole ANIMICA (we convert to base units)
 *   poolAddr    — destination reward-pool address from server config
 */
export async function depositToRewardPool(req: {
  poolAddr: string;
  amountAnim: string;
  memo?: string;
}): Promise<{ txHash: string }> {
  const provider = await getProvider();
  const amount = animToBaseUnits(req.amountAnim);
  return provider.sendTransfer({
    to: req.poolAddr,
    amount,
    memo: req.memo ?? "academy reward-pool deposit",
  });
}

// 1 ANIMICA = 10^9 base units. Local conversion so the user types
// "100" not "100000000000". Throws on non-numeric to avoid silent zeroing.
export function animToBaseUnits(input: string): string {
  const trimmed = input.trim();
  if (!/^\d+(\.\d{1,9})?$/.test(trimmed)) {
    throw new Error(
      "amount must be a positive decimal with up to 9 fractional digits",
    );
  }
  const [intPart, fracPart = ""] = trimmed.split(".");
  const paddedFrac = (fracPart + "000000000").slice(0, 9);
  const combined = (intPart + paddedFrac).replace(/^0+/, "") || "0";
  return combined;
}

export function baseUnitsToAnim(units: string | number | bigint): string {
  const s = typeof units === "string" ? units : units.toString();
  if (!/^\d+$/.test(s)) return "0";
  if (s.length <= 9) {
    return "0." + s.padStart(9, "0").replace(/0+$/, "") || "0";
  }
  const intPart = s.slice(0, -9);
  const fracPart = s.slice(-9).replace(/0+$/, "");
  return fracPart ? `${intPart}.${fracPart}` : intPart;
}

export function shortAddr(addr: string, n = 8): string {
  return addr.length > n * 2 + 3 ? `${addr.slice(0, n)}…${addr.slice(-4)}` : addr;
}
