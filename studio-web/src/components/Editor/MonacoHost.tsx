import * as React from "react";

/**
 * Lightweight, lazy-loaded Monaco wrapper.
 *
 * - No SSR issues (imports monaco only on client)
 * - Respects container resize (ResizeObserver)
 * - Maintains a stable model by path/URI so language services work
 * - Applies a theme derived from CSS variables (falls back to sensible defaults)
 * - Debounced onChange to avoid chatty renders
 */

type MonacoNS = typeof import("monaco-editor");

type EditorOptions = Record<string, unknown>;

export type MonacoHostProps = {
  /** Source text */
  value: string;
  /** e.g. "python", "json", "typescript", "plaintext" */
  language: string;
  /** Logical file path for Monaco model URI (improves language services) */
  path?: string;
  /** Readonly view */
  readOnly?: boolean;
  /** Extra options passed to monaco.editor.create(...) */
  options?: EditorOptions;
  /** Called (debounced) when editor content changes */
  onChange?: (value: string) => void;
  /** Called after editor is created */
  onMount?: (monaco: MonacoNS, editor: import("monaco-editor").editor.IStandaloneCodeEditor) => void;
  /** Debounce delay for onChange in ms (default 150) */
  debounceMs?: number;
  /** Class for outer container */
  className?: string;
  /** Inline style height/width (defaults: 100%/auto via CSS) */
  style?: React.CSSProperties;
};

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function defineTheme(monaco: MonacoNS, isDark: boolean) {
  const bg = cssVar("--code-bg", isDark ? "#0f1117" : "#ffffff");
  const fg = cssVar("--code-fg", isDark ? "#e6edf3" : "#1f2328");
  const sel = cssVar("--code-selection", isDark ? "rgba(56,139,253,0.3)" : "rgba(56,139,253,0.2)");
  const guide = cssVar("--code-guide", isDark ? "#2b303b" : "#e5e7eb");

  monaco.editor.defineTheme("animica-theme", {
    base: isDark ? "vs-dark" : "vs",
    inherit: true,
    rules: [
      { token: "", foreground: fg.replace("#", ""), background: bg.replace("#", "") },
      { token: "comment", foreground: isDark ? "7d8590" : "6e7781" },
      { token: "string", foreground: isDark ? "a5d6ff" : "0a3069" },
      { token: "number", foreground: isDark ? "ffa657" : "953800" },
      { token: "keyword", foreground: isDark ? "ff7b72" : "cf222e", fontStyle: "bold" },
      { token: "type", foreground: isDark ? "79c0ff" : "0550ae" },
      { token: "delimiter", foreground: isDark ? "c9d1d9" : "57606a" },
    ],
    colors: {
      "editor.background": bg,
      "editor.foreground": fg,
      "editor.selectionBackground": sel,
      "editor.lineHighlightBackground": isDark ? "#2b303b80" : "#e5e7eb80",
      "editorLineNumber.foreground": isDark ? "#6e7681" : "#8c959f",
      "editorIndentGuide.background": guide,
      "editorIndentGuide.activeBackground": isDark ? "#4f566b" : "#9aa4b2",
      "scrollbarSlider.background": isDark ? "#30363d" : "#d0d7de",
      "scrollbarSlider.hoverBackground": isDark ? "#484f58" : "#b6c0ca",
      "scrollbarSlider.activeBackground": isDark ? "#6e7681" : "#8c959f",
    },
  });
  monaco.editor.setTheme("animica-theme");
}

function useDebouncedCallback<T extends (...args: any[]) => void>(cb: T | undefined, ms: number) {
  const timer = React.useRef<number | null>(null);
  const saved = React.useRef<T | undefined>(cb);
  React.useEffect(() => {
    saved.current = cb;
  }, [cb]);

  const cancel = React.useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const fn = React.useCallback(
    (...args: Parameters<T>) => {
      if (!saved.current) return;
      cancel();
      timer.current = window.setTimeout(() => {
        saved.current?.(...args);
        timer.current = null;
      }, ms);
    },
    [ms, cancel]
  );

  React.useEffect(() => cancel, [cancel]);

  return fn;
}

export const MonacoHost: React.FC<MonacoHostProps> = ({
  value,
  language,
  path,
  readOnly,
  options,
  onChange,
  onMount,
  debounceMs = 150,
  className,
  style,
}) => {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const monacoRef = React.useRef<MonacoNS | null>(null);
  const editorRef = React.useRef<import("monaco-editor").editor.IStandaloneCodeEditor | null>(null);
  const modelRef = React.useRef<import("monaco-editor").editor.ITextModel | null>(null);
  const applyingExternalRef = React.useRef(false); // guard to avoid feedback loops

  const debouncedOnChange = useDebouncedCallback((v: string) => onChange?.(v), debounceMs);

  // Lazy-load monaco and build editor
  React.useEffect(() => {
    let disposed = false;

    (async () => {
      if (typeof window === "undefined") return;

      const monaco = await import("monaco-editor");
      if (disposed) return;
      monacoRef.current = monaco;

      // Theme
      const isDark =
        document.documentElement.dataset.theme === "dark" ||
        window.matchMedia?.("(prefers-color-scheme: dark)").matches ||
        false;
      defineTheme(monaco, !!isDark);

      // Create/get model by URI
      const uri = monaco.Uri.parse(`inmemory:${path ?? "untitled"}`);
      let model = monaco.editor.getModel(uri);
      if (!model) {
        model = monaco.editor.createModel(value ?? "", language ?? "plaintext", uri);
      } else {
        // Keep language in sync if reusing existing model
        monaco.editor.setModelLanguage(model, language ?? "plaintext");
        model.setValue(value ?? "");
      }
      modelRef.current = model;

      // Create editor
      const editor = monaco.editor.create(containerRef.current as HTMLElement, {
        model,
        readOnly: !!readOnly,
        automaticLayout: false, // we handle with ResizeObserver (faster & precise)
        wordWrap: "on",
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        tabSize: 4,
        insertSpaces: true,
        fontLigatures: true,
        lineNumbers: "on",
        ...options,
      } as any);
      editorRef.current = editor;

      // Change listener
      const sub = editor.onDidChangeModelContent(() => {
        if (applyingExternalRef.current) return;
        const v = editor.getValue();
        debouncedOnChange(v);
      });

      // Resize handling
      const ro = new ResizeObserver(() => {
        editor.layout();
      });
      if (containerRef.current) ro.observe(containerRef.current);

      // Theme re-apply on system scheme change
      const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
      const onScheme = () => {
        defineTheme(monaco, mq?.matches ?? false);
      };
      mq?.addEventListener?.("change", onScheme);

      // Fire onMount
      onMount?.(monaco, editor);

      // Cleanup
      return () => {
        disposed = true;
        mq?.removeEventListener?.("change", onScheme);
        sub.dispose();
        ro.disconnect();
        editor.dispose();
        // Note: model may be shared by path across hosts; dispose only if no other editors use it.
        // Safer to leave it alive; Monaco GC will reap when no editors reference it.
        editorRef.current = null;
      };
    })();

    return () => {
      // mark disposed flag for async continuation branch
      // actual disposals are done in the async cleanup above
      // (this outer cleanup is here in case the async task hasn't run yet)
    };
  }, []); // mount once

  // External value updates (prop -> editor)
  React.useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const current = editor.getValue();
    if (value !== current) {
      applyingExternalRef.current = true;
      const pos = editor.getPosition();
      editor.executeEdits("prop-update", [
        {
          range: editor.getModel()!.getFullModelRange(),
          text: value ?? "",
        },
      ]);
      if (pos) editor.setPosition(pos);
      applyingExternalRef.current = false;
    }
  }, [value]);

  // Language change
  React.useEffect(() => {
    const monaco = monacoRef.current;
    const model = modelRef.current;
    if (!monaco || !model) return;
    const lang = language || "plaintext";
    if (model.getLanguageId() !== lang) {
      monaco.editor.setModelLanguage(model, lang);
    }
  }, [language]);

  // Path/URI change => migrate or create a new model, keep content
  React.useEffect(() => {
    const monaco = monacoRef.current;
    const editor = editorRef.current;
    const currentModel = modelRef.current;
    if (!monaco || !editor || !currentModel) return;

    const newUri = monaco.Uri.parse(`inmemory:${path ?? "untitled"}`);
    if (currentModel.uri.toString() === newUri.toString()) return;

    let next = monaco.editor.getModel(newUri);
    if (!next) {
      next = monaco.editor.createModel(currentModel.getValue(), language ?? "plaintext", newUri);
    }
    modelRef.current = next;
    editor.setModel(next);
  }, [path]);

  // Read-only toggle
  React.useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.updateOptions({ readOnly: !!readOnly });
  }, [readOnly]);

  // Options update (shallow)
  React.useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !options) return;
    editor.updateOptions(options as any);
  }, [options]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        ...style,
      }}
    />
  );
};

export default MonacoHost;
