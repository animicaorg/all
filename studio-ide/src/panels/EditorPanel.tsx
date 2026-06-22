import { useFilesStore } from "@/state/files";
import { useUiStore } from "@/state/ui";
import { EditorTabs } from "@/components/editor/EditorTabs";
import { CodeEditor } from "@/components/editor/CodeEditor";

export function EditorPanel() {
  const { activePath, byPath, setContent, save } = useFilesStore();
  const { openFiles } = useUiStore();

  const entry = activePath ? byPath[activePath] : undefined;

  return (
    <div className="flex h-full flex-col">
      <EditorTabs />
      {!activePath ? (
        <EmptyEditor onBrowse={openFiles} />
      ) : entry?.loading ? (
        <div className="grid flex-1 place-items-center text-sm text-muted">Opening file…</div>
      ) : entry?.error ? (
        <div className="grid flex-1 place-items-center px-6 text-center text-sm text-danger">{entry.error}</div>
      ) : entry?.truncated ? (
        <div className="grid flex-1 place-items-center px-6 text-center text-sm text-muted">
          This file is binary or too large to edit here.
        </div>
      ) : entry?.content !== undefined ? (
        <>
          <div className="flex flex-none items-center justify-between border-b border-border px-3 py-1.5">
            <span className="truncate text-xs text-muted">{activePath}</span>
            <button
              className="btn-primary btn-sm"
              disabled={!entry.dirty || entry.saving}
              onClick={() => activePath && void save(activePath)}
            >
              {entry.saving ? "Saving…" : entry.dirty ? "Save" : "Saved"}
            </button>
          </div>
          <div className="min-h-0 flex-1">
            <CodeEditor
              key={activePath}
              path={activePath}
              value={entry.content}
              onChange={(v) => activePath && setContent(activePath, v)}
              onSave={() => activePath && void save(activePath)}
            />
          </div>
        </>
      ) : (
        <div className="grid flex-1 place-items-center text-sm text-muted">No content.</div>
      )}
    </div>
  );
}

function EmptyEditor({ onBrowse }: { onBrowse: () => void }) {
  return (
    <div className="grid flex-1 place-items-center px-6 text-center">
      <div className="max-w-xs">
        <h2 className="text-base font-semibold">No file open</h2>
        <p className="mt-1 text-sm text-muted">Pick a file from the tree to start editing.</p>
        <button className="btn-ghost btn-sm mt-4" onClick={onBrowse}>
          Browse files
        </button>
      </div>
    </div>
  );
}
