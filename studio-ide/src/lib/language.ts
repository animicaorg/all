// Map a file path to a CodeMirror 6 language extension using the installed
// @codemirror/lang-* packages. Returns [] (no language) for unknown types.
import type { Extension } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";

function ext(path: string): string {
  const base = path.split("/").pop() ?? path;
  const dot = base.lastIndexOf(".");
  return dot >= 0 ? base.slice(dot + 1).toLowerCase() : base.toLowerCase();
}

export function languageFor(path: string): Extension {
  const e = ext(path);
  switch (e) {
    case "py":
    case "pyi":
      return python();
    case "js":
    case "cjs":
    case "mjs":
      return javascript();
    case "jsx":
      return javascript({ jsx: true });
    case "ts":
      return javascript({ typescript: true });
    case "tsx":
      return javascript({ typescript: true, jsx: true });
    case "json":
    case "jsonc":
      return json();
    case "md":
    case "markdown":
    case "mdx":
      return markdown();
    case "html":
    case "htm":
      return html();
    case "css":
    case "scss":
    case "less":
      return css();
    default:
      return [];
  }
}
