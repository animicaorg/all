import * as React from "react";

import { useAnimicaWallet } from "../../hooks/useAnimicaWallet";
import { makeAicfChat, type ChatChunk } from "../../services/aicfChat";

type Tier = "tiny" | "small" | "flagship" | "large";
type Turn = {
  role: "user" | "assistant";
  text: string;
  tier?: Tier | string;
  costAnimica?: number;
  latencyMs?: number;
};

const DEFAULT_RPC_URL =
  (import.meta as ImportMeta & { env?: Record<string, string> })?.env?.VITE_AICF_RPC_URL ??
  "https://rpc.mainnet.animica.org/rpc";

/**
 * Animica Studio — Chat page.
 *
 * Connects to the user's wallet via the Animica browser extension
 * (window.animica) and charges per turn from there. The local fallback
 * (offline mode) is not enabled in this UI on purpose — the studio web
 * surface is always paid; users without a funded wallet land on the
 * connect-wallet state.
 */
export default function ChatPage() {
  const wallet = useAnimicaWallet();
  const [tier, setTier] = React.useState<Tier>("small");
  const [yolo, setYolo] = React.useState(false);
  const [prompt, setPrompt] = React.useState("");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [streaming, setStreaming] = React.useState(false);
  const [streamBuf, setStreamBuf] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const client = React.useMemo(() => makeAicfChat(DEFAULT_RPC_URL), []);

  // Scroll the transcript to the latest turn whenever it grows.
  const transcriptRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    transcriptRef.current?.scrollTo({ top: 1e9, behavior: "smooth" });
  }, [turns.length, streamBuf]);

  async function onSend(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    if (!wallet.address) {
      setError("connect your wallet first");
      return;
    }
    setError(null);
    const userText = prompt;
    setPrompt("");
    const history = turns.map((t) => ({ role: t.role, text: t.text }));
    setTurns((ts) => [...ts, { role: "user", text: userText }]);
    setStreaming(true);
    setStreamBuf("");

    try {
      // Quote first so we can show a cost preview unless --yolo.
      const quote = await client.estimateCost({
        prompt: userText,
        tierPreferred: tier,
      });
      if (
        !yolo &&
        wallet.balanceAnimica != null &&
        quote.costAnimica > wallet.balanceAnimica
      ) {
        setError(
          `quote ${quote.costAnimica.toFixed(6)} ANIMICA exceeds balance ` +
            `${wallet.balanceAnimica.toFixed(6)} — top up or enable YOLO`,
        );
        setStreaming(false);
        setTurns((ts) => ts.slice(0, -1));
        setPrompt(userText);
        return;
      }
      if (!yolo) {
        const ok = window.confirm(
          `Pay ~${quote.costAnimica.toFixed(6)} ANIMICA for this turn?\n` +
            `(tier=${quote.tier}, est. latency ${quote.latencyMs}ms)`,
        );
        if (!ok) {
          setStreaming(false);
          setTurns((ts) => ts.slice(0, -1));
          setPrompt(userText);
          return;
        }
      }

      const result = await client.runTurn(
        {
          prompt: userText,
          tierPreferred: tier,
          history: history.map((h) => ({ role: h.role, content: h.text })),
        },
        async (cost, recipient) => {
          // The wallet extension prompts the user; returns the tx hash.
          return wallet.signAndSend({
            to: recipient,
            value: cost.toFixed(8),
            memo: { kind: "aicf.payment", tier, prompt_len: userText.length },
          });
        },
        (chunk: ChatChunk) => {
          if (chunk.text) setStreamBuf((b) => b + chunk.text);
        },
      );
      setTurns((ts) => [
        ...ts,
        {
          role: "assistant",
          text: result.text,
          tier,
          costAnimica: result.actualCostAnimica,
          latencyMs: result.latencyMs,
        },
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setTurns((ts) => ts.slice(0, -1));
      setPrompt(userText);
    } finally {
      setStreaming(false);
      setStreamBuf("");
      void wallet.refreshBalance();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <Header wallet={wallet} />

      <main className="flex flex-1 min-h-0 flex-col mx-auto w-full max-w-5xl px-4 py-6 gap-4">
        {!wallet.isAvailable && <InstallExtensionPanel installUrl={wallet.installUrl} />}

        {wallet.isAvailable && !wallet.address && (
          <ConnectPanel onConnect={wallet.connect} connecting={wallet.connecting} error={wallet.error} />
        )}

        {wallet.address && (
          <>
            <Controls
              tier={tier}
              setTier={setTier}
              yolo={yolo}
              setYolo={setYolo}
              balanceAnimica={wallet.balanceAnimica}
              network={wallet.network}
            />

            <div
              ref={transcriptRef}
              className="flex-1 min-h-0 overflow-y-auto rounded border bg-white"
            >
              {turns.length === 0 && (
                <div className="p-6 text-sm text-[color:var(--muted,#6b7280)]">
                  Ask anything code-focused. Each turn pays from your connected
                  wallet to AICF; miners run the flagship model and stream the
                  answer back.
                </div>
              )}
              <ul className="divide-y">
                {turns.map((t, i) => (
                  <li key={i} className="p-4">
                    <div className="text-xs font-semibold uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-2">
                      {t.role === "user" ? "you" : "assistant"}
                    </div>
                    <div className="whitespace-pre-wrap text-sm font-mono">{t.text}</div>
                    {t.role === "assistant" && (
                      <div className="mt-2 text-xs text-[color:var(--muted,#6b7280)] font-mono">
                        tier={t.tier ?? "—"} • cost=
                        {t.costAnimica != null ? t.costAnimica.toFixed(6) : "—"} ANIMICA •
                        latency={t.latencyMs ?? "—"} ms
                      </div>
                    )}
                  </li>
                ))}
                {streaming && (
                  <li className="p-4">
                    <div className="text-xs font-semibold uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-2">
                      assistant <span className="animate-pulse">▍</span>
                    </div>
                    <div className="whitespace-pre-wrap text-sm font-mono">{streamBuf}</div>
                  </li>
                )}
              </ul>
            </div>

            {error && (
              <div className="rounded border border-red-300 bg-red-50 text-red-800 p-3 text-sm">
                {error}
              </div>
            )}

            <form onSubmit={onSend} className="flex gap-2">
              <input
                className="flex-1 border rounded px-3 py-2 text-sm"
                placeholder="Ask a coding question — wallet pays per turn…"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={streaming}
              />
              <button
                type="submit"
                disabled={streaming || !prompt.trim()}
                className="px-4 py-2 rounded bg-amber-500 hover:bg-amber-400 text-white disabled:opacity-50"
              >
                {streaming ? "sending…" : "send"}
              </button>
            </form>
          </>
        )}
      </main>
    </div>
  );
}

function Header({ wallet }: { wallet: ReturnType<typeof useAnimicaWallet> }) {
  return (
    <header className="border-b bg-white">
      <div className="mx-auto max-w-5xl px-4 py-3 flex items-center gap-3 text-sm">
        <h1 className="font-semibold">Studio · Chat</h1>
        <span className="text-[color:var(--muted,#6b7280)]">·</span>
        <span className="text-[color:var(--muted,#6b7280)]">
          AICF-paid distributed inference
        </span>
        <span className="flex-1" />
        {wallet.address ? (
          <span className="font-mono text-xs">
            {wallet.address.slice(0, 8)}…{wallet.address.slice(-4)} ·{" "}
            <strong>
              {wallet.balanceAnimica != null
                ? wallet.balanceAnimica.toFixed(4)
                : "—"}
            </strong>{" "}
            ANIMICA
          </span>
        ) : (
          <span className="text-xs text-[color:var(--muted,#6b7280)]">no wallet</span>
        )}
      </div>
    </header>
  );
}

function InstallExtensionPanel({ installUrl }: { installUrl: string }) {
  return (
    <section className="rounded-lg border border-amber-300 bg-amber-50 p-6">
      <h2 className="font-semibold text-amber-900">
        Install the Animica wallet extension
      </h2>
      <p className="mt-2 text-sm text-amber-900/90">
        Studio Chat charges per turn from your connected wallet. The Animica
        browser extension provides a secure in-page wallet so studio.animica.org
        never sees your keys.
      </p>
      <a
        href={installUrl}
        target="_blank"
        rel="noreferrer"
        className="mt-4 inline-block rounded bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium px-4 py-2"
      >
        Install extension →
      </a>
    </section>
  );
}

function ConnectPanel({
  onConnect,
  connecting,
  error,
}: {
  onConnect: () => void;
  connecting: boolean;
  error: string | null;
}) {
  return (
    <section className="rounded-lg border bg-white p-6">
      <h2 className="font-semibold">Connect your wallet</h2>
      <p className="mt-2 text-sm text-[color:var(--muted,#6b7280)]">
        Studio Chat needs read-only access to your active address + permission
        to request signatures when you send a turn.
      </p>
      <button
        onClick={onConnect}
        disabled={connecting}
        className="mt-4 inline-block rounded bg-amber-500 hover:bg-amber-400 text-white text-sm font-medium px-4 py-2 disabled:opacity-50"
      >
        {connecting ? "approving…" : "Connect wallet"}
      </button>
      {error && <div className="mt-3 text-sm text-red-700">{error}</div>}
    </section>
  );
}

function Controls({
  tier,
  setTier,
  yolo,
  setYolo,
  balanceAnimica,
  network,
}: {
  tier: Tier;
  setTier: (t: Tier) => void;
  yolo: boolean;
  setYolo: (b: boolean) => void;
  balanceAnimica: number | null;
  network: string | null;
}) {
  return (
    <section className="rounded-lg border bg-white p-3 flex flex-wrap gap-3 items-center text-sm">
      <label className="flex items-center gap-2">
        <span className="text-[color:var(--muted,#6b7280)]">Tier</span>
        <select
          className="border rounded px-2 py-1 bg-white"
          value={tier}
          onChange={(e) => setTier(e.target.value as Tier)}
        >
          <option value="tiny">tiny — CPU, fastest, cheapest</option>
          <option value="small">small — 7B, single GPU</option>
          <option value="flagship">flagship — 16B MoE, recommended</option>
          <option value="large">large — datacenter</option>
        </select>
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={yolo}
          onChange={(e) => setYolo(e.target.checked)}
        />
        <span className="text-[color:var(--muted,#6b7280)]">
          YOLO — skip per-turn confirmation
        </span>
      </label>
      <span className="flex-1" />
      <span className="text-xs font-mono text-[color:var(--muted,#6b7280)]">
        network={network ?? "—"} · balance=
        {balanceAnimica != null ? balanceAnimica.toFixed(6) : "—"}
      </span>
    </section>
  );
}
