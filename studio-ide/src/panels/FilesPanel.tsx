import { useUiStore } from "@/state/ui";
import { useGithubStore } from "@/state/github";
import { useFilesStore } from "@/state/files";
import { FileTree } from "@/components/files/FileTree";
import { useBreakpoint } from "@/lib/breakpoint";

export function FilesPanel() {
  const { currentRepo, currentBranch } = useGithubStore();
  const { loadTree } = useFilesStore();
  const { setPanel } = useUiStore();
  const mobile = useBreakpoint() === "sm";

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{currentRepo}</div>
          {currentBranch && <div className="text-xs text-muted">{currentBranch}</div>}
        </div>
        <button
          className="btn-ghost btn-sm"
          onClick={() => void loadTree()}
          aria-label="Refresh files"
        >
          Refresh
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <FileTree onOpenFile={() => mobile && setPanel("editor")} />
      </div>
    </div>
  );
}
