/**
 * polyfills/browser.ts
 * --------------------
 * Tiny, dependency-free shims for older browsers / webviews.
 * Loaded for side effects. Safe to import multiple times.
 *
 * What we polyfill (only if missing):
 *  - globalThis (best-effort)
 *  - queueMicrotask (Promise-based)
 *  - TextEncoder / TextDecoder (UTF-8 via encodeURIComponent trick)
 *  - crypto.getRandomValues (falls back to msCrypto / Math.random as last resort)
 *  - atob / btoa (Base64)
 *  - performance.now (Date.now fallback)
 *  - structuredClone (JSON deep clone fallback; caveats apply)
 */

declare const escape: (s: string) => string;
declare const unescape: (s: string) => string;

(function initPolyfills() {
  const _g: any =
    typeof globalThis !== "undefined"
      ? (globalThis as any)
      : (function () {
          try {
            // eslint-disable-next-line no-new-func
            return Function("return this")() || (typeof self !== "undefined" ? self : (typeof window !== "undefined" ? window : {}));
          } catch {
            return typeof self !== "undefined" ? self : (typeof window !== "undefined" ? window : {});
          }
        })();

  /* ------------------------------- globalThis ------------------------------ */
  if (!_g.globalThis) {
    _g.globalThis = _g;
  }

  /* ------------------------------ queueMicrotask --------------------------- */
  if (typeof _g.queueMicrotask !== "function") {
    _g.queueMicrotask = function (cb: () => void) {
      Promise.resolve()
        .then(cb)
        .catch((e) => setTimeout(() => { throw e; }, 0));
    };
  }

  /* -------------------------- TextEncoder / Decoder ------------------------ */
  // Light, UTF-8-only polyfills if missing. Adequate for most SDK payloads.
  if (typeof _g.TextEncoder !== "function") {
    class PolyfillTextEncoder {
      encode(input: string): Uint8Array {
        // UTF-8 via encodeURIComponent + unescape trick
        const bin = unescape(encodeURIComponent(input));
        const out = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
        return out;
      }
    }
    _g.TextEncoder = PolyfillTextEncoder;
  }
  if (typeof _g.TextDecoder !== "function") {
    class PolyfillTextDecoder {
      decode(input?: ArrayBuffer | ArrayBufferView | Uint8Array | null): string {
        if (!input) return "";
        const view = input instanceof Uint8Array ? input : new Uint8Array((input as ArrayBufferView).buffer ?? (input as ArrayBuffer));
        let bin = "";
        for (let i = 0; i < view.length; i++) bin += String.fromCharCode(view[i]);
        // Reverse of the encoder trick
        return decodeURIComponent(escape(bin));
      }
    }
    _g.TextDecoder = PolyfillTextDecoder;
  }

  /* ----------------------------- crypto randomness ------------------------- */
  if (!_g.crypto) {
    // IE11 exposes msCrypto
    _g.crypto = _g.msCrypto || {};
  }
  if (typeof _g.crypto.getRandomValues !== "function") {
    if (_g.msCrypto && typeof _g.msCrypto.getRandomValues === "function") {
      _g.crypto.getRandomValues = function (buf: Uint8Array) {
        return _g.msCrypto.getRandomValues(buf);
      };
    } else {
      // Last-resort insecure fallback (not cryptographically strong).
      // Prefer upgrading the environment if you see this warning.
      _g.crypto.getRandomValues = function (buf: Uint8Array) {
        console.warn("[polyfill] crypto.getRandomValues fallback using Math.random (insecure).");
        for (let i = 0; i < buf.length; i++) {
          buf[i] = Math.floor(Math.random() * 256);
        }
        return buf;
      };
    }
  }

  /* -------------------------------- atob / btoa ---------------------------- */
  // RFC 4648 Base64 polyfills (binary strings).
  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";

  if (typeof _g.btoa !== "function") {
    _g.btoa = function (input: string): string {
      let str = String(input);
      let output = "";
      for (let block = 0, charCode: number, idx = 0, map = B64; str.charAt(idx | 0) || ((map = "="), idx % 1); output += map.charAt(63 & (block >> (8 - (idx % 1) * 8)))) {
        charCode = str.charCodeAt((idx += 3 / 4));
        if (charCode > 0xff) throw new Error("btoa polyfill: invalid character (>0xFF). Use TextEncoder first.");
        block = (block << 8) | charCode;
      }
      return output;
    };
  }

  if (typeof _g.atob !== "function") {
    _g.atob = function (input: string): string {
      let str = String(input).replace(/=+$/, "");
      if (str.length % 4 === 1) throw new Error("atob polyfill: invalid base64 string.");
      let output = "";
      let bc = 0, bs = 0, idx = 0, chr: string | number;
      // eslint-disable-next-line no-cond-assign
      for (; (chr = str.charAt(idx++)); ~(chr = B64.indexOf(chr as string)) && ((bs = bc % 4 ? (bs << 6) + (chr as number) : (chr as number)), bc++ % 4)
        ? (output += String.fromCharCode(255 & (bs >> ((-2 * bc) & 6))))
        : 0) {}
      return output;
    };
  }

  /* ----------------------------- performance.now --------------------------- */
  if (!_g.performance) _g.performance = {};
  if (typeof _g.performance.now !== "function") {
    const t0 = Date.now();
    _g.performance.now = function () {
      return Date.now() - t0;
    };
  }

  /* ----------------------------- structuredClone --------------------------- */
  if (typeof _g.structuredClone !== "function") {
    _g.structuredClone = function (obj: unknown): any {
      // Caveats: loses functions, prototypes, Dates, Maps, Sets, etc.
      // Good enough for our typical JSON-like payloads.
      return JSON.parse(JSON.stringify(obj));
    };
  }
})();
