import { useState } from "react";
import { useAuthStore } from "@/state/auth";

export function AppHeader() {
  const { me, logout } = useAuthStore();
  const [menu, setMenu] = useState(false);

  return (
    <header
      className="safe-t flex flex-none items-center gap-3 border-b border-border bg-surface px-3"
      style={{ minHeight: "var(--header-h)" }}
    >
      <div className="flex items-center gap-2 text-accent">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="6.5" />
          <circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none" />
        </svg>
        <span className="text-sm font-semibold text-fg">Studio</span>
      </div>

      {/* repo pill — wired up in the GitHub increment */}
      <button className="chip max-w-[55%] truncate" title="Connect a repository">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor">
          <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.1-1.47-1.1-1.47-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2z" />
        </svg>
        <span className="truncate">No repo</span>
      </button>

      <div className="ml-auto flex items-center gap-2">
        <div className="relative">
          <button
            className="grid h-9 w-9 place-items-center rounded-full border border-border bg-elevated text-xs font-medium text-muted"
            onClick={() => setMenu((v) => !v)}
            aria-label="Account menu"
          >
            {(me?.email?.[0] || (me?.tier === "anon" ? "G" : "?")).toUpperCase()}
          </button>
          {menu && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenu(false)} />
              <div className="absolute right-0 z-20 mt-2 w-52 overflow-hidden rounded-xl border border-border bg-elevated text-sm shadow-xl">
                <div className="border-b border-border px-3 py-2 text-xs text-muted">
                  {me?.email || (me?.tier === "anon" ? "Guest session" : "Signed in")}
                </div>
                <a className="block px-3 py-2.5 hover:bg-surface" href="/desktop">
                  Open desktop Studio
                </a>
                <button className="block w-full px-3 py-2.5 text-left text-danger hover:bg-surface" onClick={logout}>
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
