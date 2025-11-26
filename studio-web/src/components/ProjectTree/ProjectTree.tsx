import * as React from "react";
import { ChevronDown, ChevronRight, Plus } from "../CodeIcon";

/** Minimal tree node shape used by the IDE */
export type TreeNode = {
  name: string;
  path: string;           // unix-style full path from project root
  kind: "file" | "dir";
  children?: TreeNode[];  // only for kind="dir"
};

export type ProjectTreeProps = {
  /** Root-level nodes (top of the project) */
  nodes: TreeNode[];
  /** Current active file path (highlights selection) */
  activePath?: string;
  /** Called on click/enter of a file (and dirs if you want to open a folder tab) */
  onOpen: (path: string) => void;
  /** Optional handlers surfaced to a context menu or toolbar */
  onCreate?: (parentPath: string, kind: "file" | "dir") => void;
  onRename?: (path: string) => void;
  onDelete?: (path: string) => void;
  /** Root label (default: "PROJECT") */
  rootLabel?: string;
  /** Optional: filter string to narrow visible entries */
  filterText?: string;
  /** If true, show a compact toolbar at the top (default true) */
  showToolbar?: boolean;
  /** Called when a folder expand/collapse toggles (persist UI state upstream if desired) */
  onToggleDir?: (path: string, expanded: boolean) => void;
};

/** Utility to filter tree by name/path while preserving directory structure */
function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  if (!query) return nodes;
  const q = query.toLowerCase();
  const walk = (n: TreeNode): TreeNode | null => {
    const selfMatch = n.name.toLowerCase().includes(q) || n.path.toLowerCase().includes(q);
    if (n.kind === "dir" && n.children?.length) {
      const kids = n.children
        .map(walk)
        .filter(Boolean) as TreeNode[];
      if (kids.length || selfMatch) return { ...n, children: kids };
      return null;
    }
    return selfMatch ? n : null;
  };
  return nodes.map(walk).filter(Boolean) as TreeNode[];
}

/** Local expand state keyed by folder path */
function useExpandState() {
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set<string>(["/"]));
  const isExpanded = React.useCallback((p: string) => expanded.has(p), [expanded]);
  const toggle = React.useCallback(
    (p: string) =>
      setExpanded(prev => {
        const next = new Set(prev);
        if (next.has(p)) next.delete(p);
        else next.add(p);
        return next;
      }),
    []
  );
  const set = React.useCallback((p: string, open: boolean) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (open) next.add(p);
      else next.delete(p);
      return next;
    });
  }, []);
  return { expanded, isExpanded, toggle, set };
}

/** Render a single tree row */
function Row({
  node,
  depth,
  activePath,
  isExpanded,
  onToggle,
  onOpen,
  onCreate,
  onRename,
  onDelete,
}: {
  node: TreeNode;
  depth: number;
  activePath?: string;
  isExpanded: (p: string) => boolean;
  onToggle: (p: string) => void;
  onOpen: (p: string) => void;
  onCreate?: (parentPath: string, kind: "file" | "dir") => void;
  onRename?: (path: string) => void;
  onDelete?: (path: string) => void;
}) {
  const active = activePath === node.path;
  const isDir = node.kind === "dir";
  const expanded = isDir && isExpanded(node.path);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (isDir) onToggle(node.path);
      else onOpen(node.path);
    } else if (e.key === " ") {
      if (isDir) {
        e.preventDefault();
        onToggle(node.path);
      }
    } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n" && onCreate && isDir) {
      e.preventDefault();
      onCreate(node.path, "file");
    } else if (e.key === "F2" && onRename) {
      e.preventDefault();
      onRename(node.path);
    } else if ((e.key === "Backspace" || e.key === "Delete") && onDelete) {
      e.preventDefault();
      onDelete(node.path);
    }
  };

  return (
    <div role="treeitem" aria-expanded={isDir ? expanded : undefined}>
      <div
        className={`flex items-center select-none rounded px-2 py-1 cursor-pointer ${
          active ? "bg-[var(--surface-2)] text-[var(--fg-strong)]" : "hover:bg-[var(--surface-1)]"
        }`}
        style={{ paddingLeft: 8 + depth * 16 }}
        onClick={() => (isDir ? onToggle(node.path) : onOpen(node.path))}
        onDoubleClick={() => onOpen(node.path)}
        onKeyDown={onKeyDown}
        tabIndex={0}
        title={node.path}
      >
        <span className="mr-1">
          {isDir ? (expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />) : (
            <span className="inline-block w-4 h-4 rounded-sm bg-current opacity-60" />
          )}
        </span>
        <span className={`truncate ${isDir ? "font-semibold" : ""}`}>{node.name}</span>

        {/* Row actions (visible on hover/focus) */}
        <span className="ml-auto gap-1 hidden group-hover:flex focus-within:flex">
          {isDir && onCreate && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              onClick={(e) => {
                e.stopPropagation();
                onCreate(node.path, "file");
              }}
              title="New file"
            >
              <Plus size={14} />
            </button>
          )}
          {onRename && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              onClick={(e) => {
                e.stopPropagation();
                onRename(node.path);
              }}
              title="Rename"
            >
              Ren
            </button>
          )}
          {onDelete && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(node.path);
              }}
              title="Delete"
            >
              Del
            </button>
          )}
        </span>
      </div>

      {isDir && expanded && node.children?.length ? (
        <div role="group">
          {node.children.map((child) => (
            <Row
              key={child.path}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              isExpanded={isExpanded}
              onToggle={onToggle}
              onOpen={onOpen}
              onCreate={onCreate}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * ProjectTree – presentational tree component.
 * Pair with your state/actions (e.g. Zustand) by passing handlers via props.
 */
export function ProjectTree({
  nodes,
  activePath,
  onOpen,
  onCreate,
  onRename,
  onDelete,
  rootLabel = "PROJECT",
  filterText,
  showToolbar = true,
  onToggleDir,
}: ProjectTreeProps) {
  const { isExpanded, toggle, set } = useExpandState();

  const safeNodes = React.useMemo(() => (Array.isArray(nodes) ? nodes : []), [nodes]);

  const toggleDir = React.useCallback(
    (p: string) => {
      const willExpand = !isExpanded(p);
      toggle(p);
      onToggleDir?.(p, willExpand);
    },
    [isExpanded, toggle, onToggleDir]
  );

  const filtered = React.useMemo(() => filterTree(safeNodes, filterText || ""), [safeNodes, filterText]);

  return (
    <div className="h-full w-full overflow-auto text-[var(--fg)]">
      {showToolbar && (
        <div className="flex items-center justify-between px-2 py-1 text-xs uppercase tracking-wide text-[var(--fg-dim)]">
          <span>{rootLabel}</span>
          {onCreate && (
            <div className="flex gap-1">
              <button
                className="px-2 py-1 rounded hover:bg-[var(--surface-1)]"
                onClick={() => onCreate("/", "file")}
                title="New file"
              >
                + File
              </button>
              <button
                className="px-2 py-1 rounded hover:bg-[var(--surface-1)]"
                onClick={() => onCreate("/", "dir")}
                title="New folder"
              >
                + Folder
              </button>
            </div>
          )}
        </div>
      )}

      <div role="tree" aria-label="Project files" className="pb-2">
        {filtered.length === 0 ? (
          <div className="px-3 py-2 text-sm text-[var(--fg-dim)]">No files.</div>
        ) : (
          filtered.map((n) => (
            <Row
              key={n.path}
              node={n}
              depth={0}
              activePath={activePath}
              isExpanded={isExpanded}
              onToggle={(p) => {
                toggleDir(p);
                // auto-expand ancestors if filtered text present (ensures visibility)
                if (filterText) set(p, true);
              }}
              onOpen={onOpen}
              onCreate={onCreate}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))
        )}
      </div>
    </div>
  );
}

export default ProjectTree;
