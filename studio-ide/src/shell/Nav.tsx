import { useUiStore, type PanelId } from "@/state/ui";

const ITEMS: { id: PanelId; label: string; icon: JSX.Element }[] = [
  { id: "files", label: "Files", icon: <IcoFiles /> },
  { id: "editor", label: "Editor", icon: <IcoCode /> },
  { id: "ena", label: "ENA", icon: <IcoSpark /> },
  { id: "terminal", label: "Term", icon: <IcoTerm /> },
  { id: "preview", label: "Run", icon: <IcoPlay /> },
];

export function Nav({ orientation }: { orientation: "horizontal" | "vertical" }) {
  const { activePanel, setPanel } = useUiStore();

  if (orientation === "vertical") {
    return (
      <nav className="flex w-16 flex-none flex-col items-center gap-1 border-r border-border bg-surface py-3">
        {ITEMS.map((it) => (
          <button
            key={it.id}
            onClick={() => setPanel(it.id)}
            aria-label={it.label}
            className={`flex w-12 flex-col items-center gap-1 rounded-xl py-2 text-[10px] transition-colors ${
              activePanel === it.id ? "bg-accent/15 text-accent" : "text-muted hover:text-fg"
            }`}
          >
            <span className="h-5 w-5">{it.icon}</span>
            {it.label}
          </button>
        ))}
      </nav>
    );
  }

  return (
    <nav className="safe-b flex flex-none items-stretch border-t border-border bg-surface">
      {ITEMS.map((it) => (
        <button
          key={it.id}
          onClick={() => setPanel(it.id)}
          aria-label={it.label}
          className={`flex flex-1 flex-col items-center justify-center gap-1 py-2 text-[10px] transition-colors ${
            activePanel === it.id ? "text-accent" : "text-muted"
          }`}
          style={{ minHeight: "var(--tabbar-h)" }}
        >
          <span className="h-5 w-5">{it.icon}</span>
          {it.label}
        </button>
      ))}
    </nav>
  );
}

function IcoFiles() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-full w-full">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}
function IcoCode() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-full w-full">
      <path d="m8 9-3 3 3 3M16 9l3 3-3 3M13 6l-2 12" />
    </svg>
  );
}
function IcoSpark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-full w-full">
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
      <circle cx="12" cy="12" r="2.2" />
    </svg>
  );
}
function IcoTerm() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-full w-full">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="m7 9 3 3-3 3M13 15h4" />
    </svg>
  );
}
function IcoPlay() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-full w-full">
      <circle cx="12" cy="12" r="9" />
      <path d="M10 9l5 3-5 3z" fill="currentColor" stroke="none" />
    </svg>
  );
}
