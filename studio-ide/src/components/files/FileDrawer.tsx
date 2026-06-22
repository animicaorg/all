import { useUiStore } from "@/state/ui";
import { useGithubStore } from "@/state/github";
import { FileTree } from "./FileTree";

// Mobile bottom-sheet wrapper around the file tree. Opening a file closes the
// sheet and switches to the editor panel.
export function FileDrawer() {
  const { filesDrawerOpen, closeFiles, setPanel } = useUiStore();
  const { currentRepo } = useGithubStore();

  if (!filesDrawerOpen) return null;

  const onOpenFile = () => {
    closeFiles();
    setPanel("editor");
  };

  return (
    <div className="fixed inset-0 z-40 flex flex-col justify-end">
      <button
        className="absolute inset-0 bg-black/50"
        onClick={closeFiles}
        aria-label="Close files"
      />
      <div className="safe-b relative z-10 max-h-[75vh] overflow-y-auto rounded-t-2xl border-t border-border bg-surface">
        <div className="sticky top-0 flex items-center justify-between border-b border-border bg-surface px-4 py-3">
          <span className="truncate text-sm font-semibold">{currentRepo ?? "Files"}</span>
          <button className="btn-ghost btn-sm" onClick={closeFiles}>
            Close
          </button>
        </div>
        <FileTree onOpenFile={onOpenFile} />
      </div>
    </div>
  );
}
