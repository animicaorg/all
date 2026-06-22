import { useMemo, useState } from "react";
import { useFilesStore } from "@/state/files";
import type { TreeNode } from "@/services/filesApi";

interface Props {
  onOpenFile?: (path: string) => void;
}

export function FileTree({ onOpenFile }: Props) {
  const { tree, treeLoading, treeError, loadTree, activePath, openFile } = useFilesStore();

  const handleOpen = (path: string) => {
    void openFile(path);
    onOpenFile?.(path);
  };

  if (treeLoading && !tree) {
    return <div className="p-4 text-sm text-muted">Loading files…</div>;
  }
  if (treeError) {
    return (
      <div className="p-4 text-sm">
        <p className="text-danger">{treeError}</p>
        <button className="btn-ghost btn-sm mt-3" onClick={() => void loadTree()}>
          Retry
        </button>
      </div>
    );
  }
  if (!tree) {
    return <div className="p-4 text-sm text-muted">No repository open.</div>;
  }

  // Root node's children are the top-level entries.
  const children = tree.children ?? [];
  return (
    <div className="py-1 text-sm">
      {children.length === 0 && <div className="px-3 py-2 text-muted">Empty repository.</div>}
      {children.map((node) => (
        <TreeRow key={node.path} node={node} depth={0} activePath={activePath} onOpenFile={handleOpen} />
      ))}
    </div>
  );
}

function TreeRow({
  node,
  depth,
  activePath,
  onOpenFile,
}: {
  node: TreeNode;
  depth: number;
  activePath: string | null;
  onOpenFile: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  const pad = { paddingLeft: `${8 + depth * 14}px` };

  // Sort dirs first, then alpha — defensive in case sidecar doesn't.
  const sorted = useMemo(() => {
    if (node.type !== "dir" || !node.children) return [];
    return [...node.children].sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [node]);

  if (node.type === "dir") {
    return (
      <div>
        <button
          className="flex w-full items-center gap-1.5 py-1.5 pr-3 text-left text-muted hover:text-fg"
          style={pad}
          onClick={() => setOpen((v) => !v)}
        >
          <svg
            viewBox="0 0 24 24"
            className={`h-3.5 w-3.5 flex-none transition-transform ${open ? "rotate-90" : ""}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="m9 6 6 6-6 6" />
          </svg>
          <span className="truncate">{node.name}</span>
        </button>
        {open && sorted.map((c) => (
          <TreeRow key={c.path} node={c} depth={depth + 1} activePath={activePath} onOpenFile={onOpenFile} />
        ))}
      </div>
    );
  }

  const active = node.path === activePath;
  return (
    <button
      className={`flex w-full items-center gap-1.5 py-1.5 pr-3 text-left ${
        active ? "bg-accent/15 text-accent" : "text-fg/90 hover:bg-elevated"
      }`}
      style={{ paddingLeft: `${8 + depth * 14 + 18}px` }}
      onClick={() => onOpenFile(node.path)}
      title={node.path}
    >
      <span className="truncate">{node.name}</span>
    </button>
  );
}
