/**
 * Ambient globals for the extension:
 * - `chrome.*` namespace (minimal fallback if @types/chrome isn't installed)
 * - `browser` alias (Firefox polyfill)
 * - `window.animica` injected provider (AIP-1193-like)
 *
 * These declarations are intentionally minimal; if you add @types/chrome,
 * they will merge and you’ll get full IntelliSense automatically.
 */

export {};

declare global {
  /**
   * In-page provider injected by the content script.
   * We reference the canonical type from src/provider/types.ts.
   * If that file changes, these ambient types stay in sync.
   */
  interface Window {
    animica?: import('../provider/types').AnimicaProvider;
  }

  /** Firefox-style global (polyfilled to call into chrome.*). */
  const browser: typeof chrome | undefined;
}

/**
 * Minimal `chrome` namespace so TypeScript recognizes usage in MV3 contexts
 * without requiring @types/chrome. If you do install @types/chrome, these
 * will merge/augment with the full definitions.
 */
declare namespace chrome {
  // Runtime APIs
  namespace runtime {
    const id: string;
    function getURL(path: string): string;
    function sendMessage<T = unknown>(
      message: unknown,
      options?: { includeTlsChannelId?: boolean },
      callback?: (response: T) => void
    ): void;
    function onMessage(
      callback: (message: unknown, sender: unknown, sendResponse: (response?: unknown) => void) => void
    ): { addListener: typeof callback };
    function onInstalled: { addListener(cb: (details: unknown) => void): void };
    function onConnect: { addListener(cb: (port: unknown) => void): void };
  }

  // Storage (sync/local/session — we use local)
  namespace storage {
    namespace local {
      function get(
        keys?: string | string[] | Record<string, unknown> | null,
        callback?: (items: Record<string, unknown>) => void
      ): void;
      function set(
        items: Record<string, unknown>,
        callback?: () => void
      ): void;
      function remove(keys: string | string[], callback?: () => void): void;
      function clear(callback?: () => void): void;
    }
    namespace session {
      function get(
        keys?: string | string[] | Record<string, unknown> | null,
        callback?: (items: Record<string, unknown>) => void
      ): void;
      function set(
        items: Record<string, unknown>,
        callback?: () => void
      ): void;
      function remove(keys: string | string[], callback?: () => void): void;
      function clear(callback?: () => void): void;
    }
    const onChanged: {
      addListener(cb: (changes: Record<string, { oldValue?: unknown; newValue?: unknown }>, areaName: string) => void): void;
    };
  }

  // Alarms (used for polling heads / housekeeping)
  namespace alarms {
    function create(name: string, info: { when?: number; delayInMinutes?: number; periodInMinutes?: number }): void;
    function clear(name: string, cb?: (wasCleared: boolean) => void): void;
    function get(name: string, cb: (alarm?: { name: string; scheduledTime: number; periodInMinutes?: number }) => void): void;
    const onAlarm: { addListener(cb: (alarm: { name: string }) => void): void };
  }

  // Action (toolbar button)
  namespace action {
    function setBadgeText(details: { text: string; tabId?: number }): void;
    function setBadgeBackgroundColor(details: { color: string | number[]; tabId?: number }): void;
    function setIcon(details: { path: Record<string, string> | string; tabId?: number }): void;
  }

  // Notifications (heads/tx status)
  namespace notifications {
    function create(
      notificationId: string,
      options: {
        type: 'basic';
        iconUrl?: string;
        title: string;
        message: string;
        contextMessage?: string;
        priority?: number;
      },
      cb?: (id: string) => void
    ): void;
    function clear(notificationId: string, cb?: (wasCleared: boolean) => void): void;
  }

  // Scripting (for content injection if needed)
  namespace scripting {
    function executeScript(details: {
      target: { tabId: number; allFrames?: boolean };
      files?: string[];
      func?: (...args: unknown[]) => unknown;
      args?: unknown[];
      world?: 'ISOLATED' | 'MAIN';
    }): Promise<unknown[]>;
  }

  // Tabs (optional; used by approval windows)
  namespace tabs {
    function create(createProperties: { url?: string; active?: boolean }, cb?: (tab: unknown) => void): void;
    function query(queryInfo: Record<string, unknown>, cb: (tabs: unknown[]) => void): void;
    function sendMessage<T = unknown>(tabId: number, message: unknown, options?: unknown, cb?: (res: T) => void): void;
  }

  // Windows (approval / onboarding popups)
  namespace windows {
    function create(
      createData: {
        url?: string | string[];
        type?: 'popup' | 'normal' | 'panel';
        focused?: boolean;
        width?: number;
        height?: number;
        top?: number;
        left?: number;
      },
      cb?: (win: unknown) => void
    ): void;
  }
}
