import { useEffect } from "react";
import { useUiStore } from "@/state/ui";
import { useGithubStore } from "@/state/github";
import { useFilesStore } from "@/state/files";
import { useScmStore } from "@/state/scm";
import { FileTree } from "@/components/files/FileTree";
import { useBreakpoint } from "@/lib/breakpoint";

export function FilesPanel() {
  const { currentRepo, currentBranch } = useGithubStore();
  const { loadTree } = useFilesStore();
  const { setPanel, openScm } = useUiStore();
  const { changedFiles, refresh } = useScmStore();
  const mobile = useBreakpoint() === "sm";

  // Keep the changed-file count fresh while the Files panel is visible.
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const count = changedFiles.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{currentRepo}</div>
          {currentBranch && <div className="text-xs text-muted">{currentBranch}</div>}
        </div>
        <div className="flex flex-none items-center gap-2">
          <button
            className="btn-ghost btn-sm relative"
            onClick={openScm}
            aria-label="Source Control"
            title="Source Control"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="6" cy="6" r="2.5" />
              <circle cx="6" cy="18" r="2.5" />
              <circle cx="18" cy="8" r="2.5" />
              <path d="M6 8.5v7M18 10.5c0 3-3 3.5-6 3.5" />
            </svg>
            {count > 0 && (
              <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-accent-fg">
                {count}
              </span>
            )}
          </button>
          <button className="btn-ghost btn-sm" onClick={() => void loadTree()} aria-label="Refresh files">
            Refresh
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <FileTree onOpenFile={() => mobile && setPanel("editor")} />
      </div>
    </div>
  );
}
