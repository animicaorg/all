import * as React from "react";
import { ChevronDown, ChevronRight, Plus } from "../CodeIcon";

/** Duplicate of the TreeNode shape used in ProjectTree to avoid circular deps. */
export type TreeNode = {
  name: string;
  path: string;           // unix-style full path from project root
  kind: "file" | "dir";
  children?: TreeNode[];  // only for kind="dir"
};

export type TreeItemProps = {
  node: TreeNode;
  /** Visual indentation level (0 for root) */
  depth: number;
  /** If true, highlight as selected */
  activePath?: string;
  /** Whether this directory is currently expanded (ignored for files) */
  expanded?: boolean;
  /** Toggle expand/collapse for directories */
  onToggle: (path: string) => void;
  /** Open the file (or directory, if you implement folder tabs) */
  onOpen: (path: string) => void;
  /** Optional row-level actions */
  onCreate?: (parentPath: string, kind: "file" | "dir") => void;
  onRename?: (path: string) => void;
  onDelete?: (path: string) => void;
  /** Optional context menu hook */
  onContextMenu?: (e: React.MouseEvent, path: string) => void;
};

/**
 * TreeItem – one row in the project tree. Accessible, keyboard-friendly,
 * visually consistent with the rest of the Studio UI.
 */
export function TreeItem({
  node,
  depth,
  activePath,
  expanded = false,
  onToggle,
  onOpen,
  onCreate,
  onRename,
  onDelete,
  onContextMenu,
}: TreeItemProps) {
  const isDir = node.kind === "dir";
  const isActive = activePath === node.path;

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDir) onToggle(node.path);
    else onOpen(node.path);
  };

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onOpen(node.path);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Basic a11y: allow keyboard navigation & actions
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
    } else if ((e.key === "ArrowRight" || e.key === "Right") && isDir && !expanded) {
      // Expand on right arrow
      e.preventDefault();
      onToggle(node.path);
    } else if ((e.key === "ArrowLeft" || e.key === "Left") && isDir && expanded) {
      // Collapse on left arrow
      e.preventDefault();
      onToggle(node.path);
    }
  };

  return (
    <div role="treeitem" aria-expanded={isDir ? expanded : undefined} aria-selected={isActive || undefined}>
      <div
        className={`group flex items-center rounded px-2 py-1 cursor-pointer select-none ${
          isActive ? "bg-[var(--surface-2)] text-[var(--fg-strong)]" : "hover:bg-[var(--surface-1)]"
        }`}
        style={{ paddingLeft: 8 + depth * 16 }}
        title={node.path}
        tabIndex={0}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onKeyDown={handleKeyDown}
        onContextMenu={(e) => {
          if (onContextMenu) onContextMenu(e, node.path);
        }}
      >
        <span className="mr-1 shrink-0">
          {isDir ? (expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />) : (
            <span className="inline-block w-4 h-4 rounded-sm bg-current opacity-60" />
          )}
        </span>

        <span className={`truncate ${isDir ? "font-semibold" : ""}`}>{node.name}</span>

        {/* Inline actions (visible on hover/focus) */}
        <span className="ml-auto hidden gap-1 group-hover:flex focus-within:flex">
          {isDir && onCreate && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              title="New file"
              onClick={(e) => {
                e.stopPropagation();
                onCreate(node.path, "file");
              }}
            >
              <Plus size={14} />
            </button>
          )}
          {onRename && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              title="Rename (F2)"
              onClick={(e) => {
                e.stopPropagation();
                onRename(node.path);
              }}
            >
              Ren
            </button>
          )}
          {onDelete && (
            <button
              className="text-xs px-1 py-0.5 rounded hover:bg-[var(--surface-2)]"
              title="Delete (Del)"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(node.path);
              }}
            >
              Del
            </button>
          )}
        </span>
      </div>
    </div>
  );
}

export default TreeItem;
