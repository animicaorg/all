import { useEffect, useRef, useState } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { openTermSocket, type TermSocket } from "@/services/termSocket";

type ConnState = "connecting" | "connected" | "disconnected";

// Theme tokens mirror styles.css :root (bg/surface/fg/muted/accent...).
const THEME = {
  background: "#0b0d12",
  foreground: "#e6e9ef",
  cursor: "#6ea8fe",
  cursorAccent: "#0b0d12",
  selectionBackground: "#252b38",
  black: "#0b0d12",
  brightBlack: "#8b93a7",
  red: "#f06e6e",
  green: "#3fb97a",
  yellow: "#e0a84f",
  blue: "#6ea8fe",
  magenta: "#b18cff",
  cyan: "#5fd0c8",
  white: "#e6e9ef",
};

// Control sequences for the mobile accessory keys.
const ESC = "\x1b";
const KEYS: { label: string; seq: string; wide?: boolean }[] = [
  { label: "Esc", seq: ESC },
  { label: "Tab", seq: "\t" },
  { label: "Ctrl C", seq: "\x03" },
  { label: "Ctrl D", seq: "\x04" },
  { label: "/", seq: "/" },
  { label: "↑", seq: ESC + "[A" },
  { label: "↓", seq: ESC + "[B" },
  { label: "←", seq: ESC + "[D" },
  { label: "→", seq: ESC + "[C" },
];

export function Terminal() {
  const hostRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Xterm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const sockRef = useRef<TermSocket | null>(null);
  const [state, setState] = useState<ConnState>("connecting");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Xterm({
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", "Roboto Mono", monospace',
      fontSize: 13,
      lineHeight: 1.2,
      cursorBlink: true,
      theme: THEME,
      scrollback: 5000,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    xtermRef.current = term;
    fitRef.current = fit;

    const safeFit = () => {
      try {
        fit.fit();
      } catch {
        /* host not measurable yet */
      }
    };
    safeFit();

    const sock = openTermSocket({
      onOpen: () => {
        setState("connected");
        safeFit();
        sock.resize(term.cols, term.rows);
        term.focus();
      },
      onData: (d) => term.write(d),
      onClose: ({ reason }) => {
        setState("disconnected");
        const msg = reason
          ? `\r\n\x1b[33m[terminal disconnected: ${reason}]\x1b[0m\r\n`
          : "\r\n\x1b[33m[terminal disconnected]\x1b[0m\r\n";
        term.write(msg);
      },
      onError: () => setState("disconnected"),
    });
    sockRef.current = sock;

    // Local input -> pty stdin.
    const dataSub = term.onData((d) => sock.send(d));

    // Keep the pty's window size in sync with the rendered grid.
    const ro = new ResizeObserver(() => {
      safeFit();
      if (sock.ready) sock.resize(term.cols, term.rows);
    });
    ro.observe(host);

    return () => {
      ro.disconnect();
      dataSub.dispose();
      sock.close();
      term.dispose();
      xtermRef.current = null;
      fitRef.current = null;
      sockRef.current = null;
    };
  }, []);

  const sendKey = (seq: string) => {
    sockRef.current?.send(seq);
    xtermRef.current?.focus();
  };

  return (
    <div className="flex h-full flex-col bg-bg">
      <div className="flex flex-none items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-lg bg-accent/15 text-accent">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 7l4 5-4 5M13 17h6" />
            </svg>
          </span>
          <span className="text-sm font-semibold">Terminal</span>
        </div>
        <StatusChip state={state} />
      </div>

      <div className="relative min-h-0 flex-1">
        <div ref={hostRef} className="absolute inset-0 px-2 py-1" />
        {state === "connecting" && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center text-xs text-muted">
            Connecting…
          </div>
        )}
      </div>

      {/* Mobile accessory key row — horizontally scrollable on small screens. */}
      <div className="safe-b flex flex-none gap-1.5 overflow-x-auto border-t border-border px-2 py-2">
        {KEYS.map((k) => (
          <button
            key={k.label}
            className="btn-ghost btn-sm flex-none font-mono"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => sendKey(k.seq)}
          >
            {k.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function StatusChip({ state }: { state: ConnState }) {
  const map = {
    connecting: { dot: "bg-warn", label: "Connecting" },
    connected: { dot: "bg-ok", label: "Connected" },
    disconnected: { dot: "bg-danger", label: "Disconnected" },
  }[state];
  return (
    <span className="chip">
      <span className={`h-2 w-2 rounded-full ${map.dot}`} />
      {map.label}
    </span>
  );
}
