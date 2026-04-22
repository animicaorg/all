export type MonacoChangeDisposable = {
  dispose: () => void;
};

export type MonacoEditorInstance = {
  dispose: () => void;
  getValue: () => string;
  layout: () => void;
  onDidChangeModelContent: (listener: () => void) => MonacoChangeDisposable;
  setValue: (value: string) => void;
  updateOptions: (options: Record<string, unknown>) => void;
};

export type MonacoNamespace = {
  editor: {
    create: (element: HTMLElement, options: Record<string, unknown>) => MonacoEditorInstance;
    defineTheme: (name: string, data: Record<string, unknown>) => void;
    setTheme: (theme: string) => void;
  };
};

type AmdRequire = ((modules: string[], callback: (module: unknown) => void) => void) & {
  config: (config: Record<string, unknown>) => void;
};

declare global {
  interface Window {
    monaco?: MonacoNamespace;
    require?: AmdRequire;
  }
}

const MONACO_SCRIPT_ID = 'aicf-monaco-loader';
const MONACO_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs';

let monacoPromise: Promise<MonacoNamespace> | null = null;

function appendLoaderScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(MONACO_SCRIPT_ID) as HTMLScriptElement | null;
    if (existing) {
      if ((existing as any).dataset.loaded === 'true') {
        resolve();
        return;
      }
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('Failed to load Monaco loader script')), { once: true });
      return;
    }

    const script = document.createElement('script');
    script.id = MONACO_SCRIPT_ID;
    script.src = `${MONACO_BASE}/loader.js`;
    script.async = true;
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true';
      resolve();
    });
    script.addEventListener('error', () => reject(new Error('Failed to load Monaco loader script')));
    document.head.appendChild(script);
  });
}

export function loadMonaco(): Promise<MonacoNamespace> {
  if (window.monaco) {
    return Promise.resolve(window.monaco);
  }

  if (monacoPromise) {
    return monacoPromise;
  }

  monacoPromise = appendLoaderScript()
    .then(
      () =>
        new Promise<MonacoNamespace>((resolve, reject) => {
          const amdRequire = window.require;
          if (!amdRequire) {
            reject(new Error('Monaco AMD loader is unavailable'));
            return;
          }

          amdRequire.config({
            paths: {
              vs: MONACO_BASE
            }
          });

          amdRequire(['vs/editor/editor.main'], () => {
            if (!window.monaco) {
              reject(new Error('Monaco did not initialize'));
              return;
            }
            resolve(window.monaco);
          });
        })
    )
    .catch((error) => {
      monacoPromise = null;
      throw error;
    });

  return monacoPromise;
}
