import { useEffect, useRef } from "react";
import { EditorView, lineNumbers } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { unifiedMergeView } from "@codemirror/merge";
import { syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";
import { languageFor } from "@/lib/language";

// Read-only unified diff: the editor doc is `newText`, compared against the
// `original` (oldText). Inserted/deleted chunks are highlighted by @codemirror/merge.
const cssVar = (name: string) => `rgb(var(${name}))`;

const diffTheme = EditorView.theme(
  {
    "&": { color: cssVar("--fg"), backgroundColor: cssVar("--bg"), fontSize: "12.5px" },
    ".cm-scroller": {
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      lineHeight: "1.55",
    },
    ".cm-gutters": { backgroundColor: cssVar("--bg"), color: cssVar("--muted"), border: "none" },
    ".cm-content": { caretColor: "transparent" },
    // merge change markers
    ".cm-changedLine": { backgroundColor: "rgb(var(--ok) / 0.12)" },
    ".cm-deletedChunk": { backgroundColor: "rgb(var(--danger) / 0.12)" },
    ".cm-changedText": { backgroundColor: "rgb(var(--ok) / 0.25)" },
    ".cm-deletedText": { backgroundColor: "rgb(var(--danger) / 0.28)" },
    // hide the per-chunk accept/reject buttons — approval is at the proposal level
    ".cm-changeButtons": { display: "none" },
  },
  { dark: true },
);

export function DiffView({ path, oldText, newText }: { path: string; oldText: string; newText: string }) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const state = EditorState.create({
      doc: newText,
      extensions: [
        lineNumbers(),
        EditorView.editable.of(false),
        EditorState.readOnly.of(true),
        EditorView.lineWrapping,
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        languageFor(path),
        unifiedMergeView({ original: oldText, mergeControls: false, gutter: true }),
        diffTheme,
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    return () => view.destroy();
  }, [path, oldText, newText]);

  return <div ref={hostRef} className="max-h-64 overflow-auto" />;
}
