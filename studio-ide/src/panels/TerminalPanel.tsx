import { useAuthStore } from "@/state/auth";
import { Terminal } from "@/components/terminal/Terminal";

export function TerminalPanel() {
  const me = useAuthStore((s) => s.me);

  // Terminal access is for authenticated sessions only (Inc 4-5 decision).
  if (me?.tier === "anon") {
    return (
      <div className="grid h-full place-items-center px-6">
        <div className="max-w-sm text-center">
          <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent/15 text-accent">
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 7l4 5-4 5M13 17h6" />
            </svg>
          </div>
          <h2 className="text-base font-semibold">Sign in to use the terminal</h2>
          <p className="mt-1 text-sm text-muted">
            The terminal runs a shell in your private sandbox container. Sign in to enable it.
          </p>
        </div>
      </div>
    );
  }

  return <Terminal />;
}
