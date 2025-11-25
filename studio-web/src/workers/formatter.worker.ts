/* eslint-disable no-restricted-globals */
/**
 * Off-thread code formatter worker.
 * - Lazily loads Prettier (standalone + plugins) when first needed.
 * - Supports: typescript, javascript, json, css, html, markdown, yaml.
 * - Python falls back to a lightweight normalizer (browser-safe).
 *
 * Message contract:
 *   postMessage({
 *     id: string|number,
 *     lang: 'typescript'|'javascript'|'json'|'css'|'html'|'markdown'|'yaml'|'python',
 *     code: string,
 *     options?: Record<string, unknown>
 *   })
 *
 * Response:
 *   { id, ok: true, formatted, diagnostics? } | { id, ok: false, error }
 */

type Lang =
  | 'typescript'
  | 'javascript'
  | 'json'
  | 'css'
  | 'html'
  | 'markdown'
  | 'yaml'
  | 'python';

type FormatRequest = {
  id: string | number;
  lang: Lang;
  code: string;
  options?: Record<string, unknown>;
};

type FormatSuccess = { id: string | number; ok: true; formatted: string; diagnostics?: string[] };
type FormatFailure = { id: string | number; ok: false; error: string };
type FormatResponse = FormatSuccess | FormatFailure;

let prettierLoaded = false;
let prettier: any = null;
let prettierPlugins: any[] = [];

/** Soft guard: skip extremely large inputs to avoid janking the UI. */
const MAX_CODE_BYTES = 1024 * 1024; // 1 MiB

const defaultPrettierOptions = {
  semi: true,
  singleQuote: true,
  trailingComma: 'es5',
  bracketSpacing: true,
  printWidth: 100,
  tabWidth: 2,
  useTabs: false
} as const;

const parserByLang: Record<Exclude<Lang, 'python'>, string> = {
  typescript: 'typescript',
  javascript: 'babel',
  json: 'json',
  css: 'css',
  html: 'html',
  markdown: 'markdown',
  yaml: 'yaml'
};

async function ensurePrettier(): Promise<boolean> {
  if (prettierLoaded) return !!prettier;
  try {
    // Lazy ESM imports (bundled by Vite)
    const [
      p,
      babel,
      ts,
      estree,
      postcss,
      md,
      html,
      yaml
    ] = await Promise.all([
      import('prettier/standalone'),
      import('prettier/plugins/babel'),
      import('prettier/plugins/typescript'),
      import('prettier/plugins/estree'),
      import('prettier/plugins/postcss'),
      import('prettier/plugins/markdown'),
      import('prettier/plugins/html'),
      import('prettier/plugins/yaml')
    ]);
    prettier = p.default || p;
    // Some plugins rely on estree; include it last.
    prettierPlugins = [
      (babel as any).default || babel,
      (ts as any).default || ts,
      (postcss as any).default || postcss,
      (md as any).default || md,
      (html as any).default || html,
      (yaml as any).default || yaml,
      (estree as any).default || estree
    ];
    prettierLoaded = true;
    return true;
  } catch {
    // Leave prettier=null; we'll fall back where possible.
    prettierLoaded = true;
    return false;
  }
}

/** Minimal, deterministic Python normalizer (safe in browser). */
function normalizePython(code: string): { formatted: string; diagnostics: string[] } {
  const diagnostics: string[] = [];
  // Convert tabs to 4 spaces; trim trailing spaces; ensure LF line endings.
  const lines = code.replace(/\r\n?/g, '\n').split('\n');
  const fixed = lines.map((line, i) => {
    if (/\t/.test(line)) diagnostics.push(`Line ${i + 1}: tabs replaced with 4 spaces.`);
    return line.replace(/\t/g, '    ').replace(/[ \t]+$/g, '');
  });
  let out = fixed.join('\n');
  if (!out.endsWith('\n')) out += '\n';
  return { formatted: out, diagnostics };
}

/** JSON fallback when Prettier is unavailable. */
function fallbackFormatJSON(code: string): { formatted: string; diagnostics: string[] } {
  const diagnostics: string[] = [];
  try {
    const obj = JSON.parse(code);
    return { formatted: JSON.stringify(obj, null, 2) + '\n', diagnostics };
  } catch (e: any) {
    diagnostics.push(`JSON parse failed: ${e?.message || String(e)}`);
    // Return input untouched to avoid destructive edits.
    return { formatted: code, diagnostics };
  }
}

async function formatWithPrettier(lang: Exclude<Lang, 'python'>, code: string, options?: Record<string, unknown>) {
  const parser = parserByLang[lang];
  const ok = await ensurePrettier();
  if (!ok || !prettier) {
    if (lang === 'json') return fallbackFormatJSON(code);
    // No Prettier -> just normalize whitespace endings for safety.
    return { formatted: (code.endsWith('\n') ? code : code + '\n'), diagnostics: ['Prettier unavailable; returned normalized code only.'] };
  }
  const formatted = await prettier.format(code, {
    ...defaultPrettierOptions,
    ...(options || {}),
    parser,
    plugins: prettierPlugins
  });
  return { formatted, diagnostics: [] as string[] };
}

self.addEventListener('message', async (evt: MessageEvent<FormatRequest>) => {
  const { id, lang, code, options } = evt.data || ({} as FormatRequest);
  const respond = (payload: FormatResponse) => {
    (self as unknown as Worker).postMessage(payload);
  };

  try {
    if (typeof id === 'undefined' || typeof code !== 'string' || !lang) {
      respond({ id: id ?? 'unknown', ok: false, error: 'Invalid format request.' });
      return;
    }
    if (new Blob([code]).size > MAX_CODE_BYTES) {
      respond({ id, ok: false, error: `Code too large to format off-thread (>${MAX_CODE_BYTES} bytes).` });
      return;
    }

    if (lang === 'python') {
      const { formatted, diagnostics } = normalizePython(code);
      respond({ id, ok: true, formatted, diagnostics: diagnostics.length ? diagnostics : undefined });
      return;
    }

    const { formatted, diagnostics } = await formatWithPrettier(lang, code, options);
    respond({ id, ok: true, formatted, diagnostics: diagnostics.length ? diagnostics : undefined });
  } catch (err: any) {
    respond({
      id: evt.data?.id ?? 'unknown',
      ok: false,
      error: err?.message || String(err)
    });
  }
});
