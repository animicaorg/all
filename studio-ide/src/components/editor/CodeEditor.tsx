import { useEffect, useRef } from "react";
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from "@codemirror/view";
import { EditorState, Compartment, type Extension } from "@codemirror/state";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { syntaxHighlighting, defaultHighlightStyle, indentOnInput, bracketMatching } from "@codemirror/language";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { languageFor } from "@/lib/language";

// Minimal dark theme that reads from the app's CSS color tokens.
const cssVar = (name: string) => `rgb(var(${name}))`;

const darkTheme = EditorView.theme(
  {
    "&": {
      color: cssVar("--fg"),
      backgroundColor: cssVar("--bg"),
      height: "100%",
      fontSize: "14px",
    },
    ".cm-scroller": {
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      lineHeight: "1.6",
      WebkitOverflowScrolling: "touch",
    },
    ".cm-content": { caretColor: cssVar("--accent"), paddingBottom: "40vh" },
    "&.cm-focused .cm-cursor": { borderLeftColor: cssVar("--accent") },
    ".cm-gutters": {
      backgroundColor: cssVar("--bg"),
      color: cssVar("--muted"),
      border: "none",
    },
    ".cm-activeLine": { backgroundColor: "rgb(var(--elevated) / 0.5)" },
    ".cm-activeLineGutter": { backgroundColor: "rgb(var(--elevated) / 0.5)", color: cssVar("--fg") },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "rgb(var(--accent) / 0.25)",
    },
    ".cm-matchingBracket": { backgroundColor: "rgb(var(--accent) / 0.2)", outline: "none" },
  },
  { dark: true },
);

interface Props {
  path: string;
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  readOnly?: boolean;
}

export function CodeEditor({ path, value, onChange, onSave, readOnly }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langCompartment = useRef(new Compartment());
  const roCompartment = useRef(new Compartment());

  // Keep latest callbacks in refs so the editor isn't rebuilt on every render.
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Create the view once.
  useEffect(() => {
    if (!hostRef.current) return;

    const saveKeymap = keymap.of([
      {
        key: "Mod-s",
        preventDefault: true,
        run: () => {
          onSaveRef.current();
          return true;
        },
      },
    ]);

    const updateListener = EditorView.updateListener.of((u) => {
      if (!u.docChanged) return;
      const text = u.state.doc.toString();
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => onChangeRef.current(text), 150);
    });

    const extensions: Extension[] = [
      lineNumbers(),
      history(),
      drawSelection(),
      indentOnInput(),
      bracketMatching(),
      closeBrackets(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      EditorView.lineWrapping,
      keymap.of([
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        indentWithTab,
      ]),
      saveKeymap,
      updateListener,
      darkTheme,
      langCompartment.current.of(languageFor(path)),
      roCompartment.current.of(EditorState.readOnly.of(!!readOnly)),
    ];

    const view = new EditorView({
      state: EditorState.create({ doc: value, extensions }),
      parent: hostRef.current,
    });
    viewRef.current = view;

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      view.destroy();
      viewRef.current = null;
    };
    // Intentionally create once; reconcile via effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reconcile external value changes (e.g. switching tabs / reload) without
  // clobbering in-progress typing.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, path]);

  // Reconcile language when the path changes.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: langCompartment.current.reconfigure(languageFor(path)) });
  }, [path]);

  // Reconcile read-only state.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ effects: roCompartment.current.reconfigure(EditorState.readOnly.of(!!readOnly)) });
  }, [readOnly]);

  return <div ref={hostRef} className="h-full w-full overflow-hidden" />;
}
