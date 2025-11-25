import * as React from "react";

/**
 * Monaco loader & theming utilities for studio-web.
 *
 * - Loads monaco-editor lazily on the client only.
 * - Configures web workers for common languages without any extra plugins.
 * - Exposes a small theming API that reads CSS variables to keep the editor
 *   visually consistent with the app (light/dark).
 * - Provides a React hook that returns the loaded monaco namespace once ready.
 */

export type MonacoNS = typeof import("monaco-editor");

let _monacoPromise: Promise<MonacoNS> | null = null;

function isClient() {
  return typeof window !== "undefined";
}

function setupMonacoEnvironment() {
  // Configure worker loaders once (Vite-friendly ESM URLs).
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore - MonacoEnvironment is a global hook monaco checks for.
  self.MonacoEnvironment = {
    getWorker(_: string, label: string) {
      if (label === "json") {
        return new Worker(
          new URL("monaco-editor/esm/vs/language/json/json.worker.js", import.meta.url),
          { type: "module" }
        );
      }
      if (label === "css" || label === "scss" || label === "less") {
        return new Worker(
          new URL("monaco-editor/esm/vs/language/css/css.worker.js", import.meta.url),
          { type: "module" }
        );
      }
      if (label === "html" || label === "handlebars" || label === "razor") {
        return new Worker(
          new URL("monaco-editor/esm/vs/language/html/html.worker.js", import.meta.url),
          { type: "module" }
        );
      }
      if (label === "typescript" || label === "javascript") {
        return new Worker(
          new URL("monaco-editor/esm/vs/language/typescript/ts.worker.js", import.meta.url),
          { type: "module" }
        );
      }
      return new Worker(
        new URL("monaco-editor/esm/vs/editor/editor.worker.js", import.meta.url),
        { type: "module" }
      );
    },
  };
}

export async function loadMonaco(): Promise<MonacoNS> {
  if (_monacoPromise) return _monacoPromise;
  if (!isClient()) {
    // SSR guard: never try to import monaco on the server.
    return Promise.reject(new Error("monaco-editor can only be loaded in the browser"));
  }
  setupMonacoEnvironment();
  _monacoPromise = import("monaco-editor");
  return _monacoPromise;
}

/** Read a CSS variable from :root with a fallback. */
function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** True if dark theme is active via data-theme or prefers-color-scheme. */
export function prefersDark(): boolean {
  if (!isClient()) return false;
  const dataTheme = document.documentElement.dataset.theme;
  if (dataTheme === "dark") return true;
  if (dataTheme === "light") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ?? false;
}

/** Define and apply the Animica Monaco theme derived from CSS variables. */
export async function applyAnimicaTheme(forceDark?: boolean, monacoNS?: MonacoNS): Promise<void> {
  if (!isClient()) return;
  const monaco = monacoNS ?? (await loadMonaco());
  const isDark = forceDark ?? prefersDark();

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

/**
 * Hook: load monaco once and keep a reference to the namespace.
 * Also applies the Animica theme and keeps it in sync with color-scheme changes.
 */
export function useMonaco() {
  const [monaco, setMonaco] = React.useState<MonacoNS | null>(null);

  React.useEffect(() => {
    let mounted = true;
    loadMonaco()
      .then(async (m) => {
        if (!mounted) return;
        setMonaco(m);
        await applyAnimicaTheme(undefined, m);
      })
      .catch(() => {
        /* swallow: consumer can guard against null */
      });

    // respond to scheme changes
    const mq = isClient() ? window.matchMedia?.("(prefers-color-scheme: dark)") : null;
    const onScheme = () => {
      if (monaco) void applyAnimicaTheme(undefined, monaco);
    };
    mq?.addEventListener?.("change", onScheme);

    return () => {
      mounted = false;
      mq?.removeEventListener?.("change", onScheme);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply theme if the namespace arrives later (first load)
  React.useEffect(() => {
    if (!monaco) return;
    void applyAnimicaTheme(undefined, monaco);
  }, [monaco]);

  return monaco;
}

/**
 * Imperative convenience: ensure monaco is loaded and themed.
 * Useful outside React.
 */
export async function ensureMonacoReady(): Promise<MonacoNS> {
  const m = await loadMonaco();
  await applyAnimicaTheme(undefined, m);
  return m;
}
