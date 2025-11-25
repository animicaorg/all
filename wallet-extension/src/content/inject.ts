/**
 * Inject the in-page provider (window.animica) by inserting a <script> tag
 * into the page's JS context. Also bootstraps the content↔page bridge.
 *
 * Why inject?
 *   Dapps expect a provider on the page's JS context (not the extension's
 *   isolated world). We add a <script> so window.animica lives alongside the
 *   dapp, while the content script acts as a secure bridge to the background.
 */

import { setupContentBridge } from "./bridge";

// Only run once per page
const INJECT_FLAG = "__animica_provider_injected__";

(function main() {
  try {
    if ((window as any)[INJECT_FLAG]) return;
    Object.defineProperty(window, INJECT_FLAG, { value: true });

    // Start the bridge (window <-> content <-> background)
    setupContentBridge();

    // Try to inject the full provider bundle (built by Vite) if present.
    // Fall back to a minimal inline stub that proxies requests via postMessage.
    tryInjectProviderBundle().catch(() => injectInlineProviderStub());
  } catch (err) {
    // As a last resort, attempt inline stub so dapps at least get a provider surface.
    try {
      injectInlineProviderStub();
    } catch {
      // swallow – no logs in production to avoid leaking details into pages
    }
  }
})();

/* ---------------------------------- Impl ---------------------------------- */

/**
 * Attempt to load the compiled in-page provider bundle. The exact filename
 * depends on the build; we try a few likely candidates.
 */
function tryInjectProviderBundle(): Promise<void> {
  const candidates = [
    // Recommended output name from our vite.config.ts
    "provider/index.js",
    // Other common shapes (keep for robustness across build changes)
    "provider.js",
    "assets/provider.js",
    "provider/index.iife.js",
  ];

  let tried = 0;

  return new Promise<void>((resolve, reject) => {
    const tryNext = () => {
      if (tried >= candidates.length) {
        reject(new Error("No provider bundle found"));
        return;
      }
      const path = candidates[tried++];
      const url = chrome.runtime.getURL(path);
      injectScriptSrc(url)
        .then(resolve)
        .catch(tryNext);
    };
    tryNext();
  });
}

/** Inject a <script src="..."> into the page context */
function injectScriptSrc(srcUrl: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = srcUrl;
    script.async = false;
    script.dataset.animica = "provider";
    script.onload = () => {
      script.remove(); // clean up tag; provider remains on window
      resolve();
    };
    script.onerror = () => {
      script.remove();
      reject(new Error("Failed to load " + srcUrl));
    };

    // Prefer <html> if present; otherwise fall back to <head> or documentElement
    const container =
      document.head || document.documentElement || document.body || document.documentElement;

    try {
      container.appendChild(script);
    } catch {
      reject(new Error("DOM append failed"));
    }
  });
}

/**
 * Minimal, CSP-friendly(ish) inline provider stub. If page CSP blocks inline
 * scripts, the external bundle path above should be used instead. This stub
 * exposes a slim AIP-1193-like surface (`request`, `on`) and proxies to the
 * content script via window.postMessage.
 */
function injectInlineProviderStub(): void {
  const code = `(function(){
    try{
      if (window.animica) return;

      const SOURCE_INPAGE = "animica:inpage";
      const SOURCE_CONTENT = "animica:content";

      class Emitter {
        constructor(){ this._l = {}; }
        on(evt, fn){ (this._l[evt] = this._l[evt] || []).push(fn); return () => this.off(evt, fn); }
        off(evt, fn){ const a=this._l[evt]; if(!a) return; const i=a.indexOf(fn); if(i>=0) a.splice(i,1); }
        emit(evt, data){ const a=this._l[evt]; if(!a) return; for(const fn of a.slice()) try{ fn(data); }catch(_){} }
      }

      class AnimicaProvider extends Emitter {
        constructor(){
          super();
          this.isAnimica = true;
          this._nextId = 1;
          this._pending = new Map();
          window.addEventListener("message", (ev) => {
            const msg = ev?.data;
            if(!msg || msg.source !== SOURCE_CONTENT) return;
            if (msg.type === "RESPONSE") {
              const p = this._pending.get(msg.id);
              if(!p) return;
              this._pending.delete(msg.id);
              if (msg.error) p.reject(Object.assign(new Error(msg.error.message||"Provider error"), { code: msg.error.code, data: msg.error.data }));
              else p.resolve(msg.result);
            } else if (msg.type === "EVENT") {
              this.emit(msg.event, msg.payload);
            }
          });
          // announce presence
          setTimeout(() => {
            window.dispatchEvent(new Event("animica#initialized"));
            this.emit("connect", { chainId: null });
          }, 0);
        }

        request(args){
          if(!args || typeof args !== "object") return Promise.reject(new Error("request: invalid args"));
          const id = this._nextId++;
          return new Promise((resolve, reject) => {
            this._pending.set(id, { resolve, reject });
            window.postMessage({ source: SOURCE_INPAGE, type: "REQUEST", id, payload: args }, "*");
          });
        }
      }

      const provider = new AnimicaProvider();
      Object.defineProperty(window, "animica", { value: provider, configurable: false, enumerable: false, writable: false });
    }catch(_){}
  })();`;

  const script = document.createElement("script");
  script.dataset.animica = "provider-inline";
  script.textContent = code;

  (document.head || document.documentElement).appendChild(script);
  script.remove();
}
