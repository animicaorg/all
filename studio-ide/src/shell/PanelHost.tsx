import { useUiStore } from "@/state/ui";

const COPY: Record<string, { title: string; body: string }> = {
  files: { title: "Files", body: "Connect a GitHub repository to browse and edit its files." },
  editor: { title: "Editor", body: "Open a file from a connected repo to start editing." },
  ena: { title: "ENA", body: "Ask the ENA agent to read and modify your code — you approve every change." },
  terminal: { title: "Terminal", body: "A full shell in your private sandbox container." },
  preview: { title: "Run & Preview", body: "Run your project and preview it live." },
};

export function PanelHost() {
  const { activePanel } = useUiStore();
  const c = COPY[activePanel];
  return (
    <div className="grid h-full place-items-center px-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent/15 text-accent">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v8M8 12h8" />
          </svg>
        </div>
        <h2 className="text-base font-semibold">{c.title}</h2>
        <p className="mt-1 text-sm text-muted">{c.body}</p>
        <p className="mt-4 text-xs text-muted">Connecting GitHub lands in the next update.</p>
      </div>
    </div>
  );
}
