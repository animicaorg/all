import { useFilesStore } from "@/state/files";

function basename(path: string): string {
  return path.split("/").pop() || path;
}

export function EditorTabs() {
  const { openTabs, activePath, byPath, setActive, closeTab } = useFilesStore();

  if (openTabs.length === 0) return null;

  return (
    <div className="flex flex-none items-stretch overflow-x-auto border-b border-border bg-surface">
      {openTabs.map((path) => {
        const entry = byPath[path];
        const active = path === activePath;
        return (
          <div
            key={path}
            className={`group flex flex-none items-center gap-1.5 border-r border-border px-3 py-2 text-xs ${
              active ? "bg-bg text-fg" : "text-muted hover:text-fg"
            }`}
          >
            <button
              className="flex items-center gap-1.5 truncate max-w-[40vw]"
              onClick={() => setActive(path)}
              title={path}
            >
              {entry?.dirty && (
                <span className="h-1.5 w-1.5 flex-none rounded-full bg-accent" aria-label="Unsaved" />
              )}
              <span className="truncate">{basename(path)}</span>
            </button>
            <button
              className="grid h-4 w-4 flex-none place-items-center rounded text-muted hover:bg-border/50 hover:text-fg"
              onClick={(e) => {
                e.stopPropagation();
                closeTab(path);
              }}
              aria-label={`Close ${basename(path)}`}
            >
              <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.4">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
        );
      })}
    </div>
  );
}
