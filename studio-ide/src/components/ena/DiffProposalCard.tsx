import { useState } from "react";
import type { Proposal } from "@/state/chat";
import { DiffView } from "@/components/ena/DiffView";
import { ApprovalBar } from "@/components/ena/ApprovalBar";

interface Props {
  proposal: Proposal;
  onResolve: (id: string, accept: boolean) => void;
}

export function DiffProposalCard({ proposal, onResolve }: Props) {
  const [openPaths, setOpenPaths] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(proposal.files.map((f, i) => [f.path, i === 0])),
  );
  const resolved = proposal.status !== "pending";

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none text-accent" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M9 3v12M15 9v12M9 9h6M15 15H9" />
          <circle cx="9" cy="3" r="0" />
        </svg>
        <span className="text-xs font-semibold">
          Proposed changes
          <span className="ml-1 font-normal text-muted">
            ({proposal.files.length} {proposal.files.length === 1 ? "file" : "files"})
          </span>
        </span>
        {proposal.status === "accepted" && <span className="ml-auto text-xs text-ok">Applied</span>}
        {proposal.status === "rejected" && <span className="ml-auto text-xs text-muted">Rejected</span>}
      </div>

      <div className="divide-y divide-border">
        {proposal.files.map((f) => {
          const open = !!openPaths[f.path];
          const isNew = !f.old_text && !!f.new_text;
          return (
            <div key={f.path}>
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-elevated"
                onClick={() => setOpenPaths((s) => ({ ...s, [f.path]: !s[f.path] }))}
              >
                <svg
                  viewBox="0 0 24 24"
                  className={`h-3.5 w-3.5 flex-none text-muted transition-transform ${open ? "rotate-90" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="m9 6 6 6-6 6" />
                </svg>
                <span className="truncate font-mono text-xs">{f.path}</span>
                {isNew && <span className="chip ml-auto !py-0.5 text-[10px] text-ok">new</span>}
              </button>
              {open && (
                <div className="border-t border-border">
                  <DiffView path={f.path} oldText={f.old_text ?? ""} newText={f.new_text ?? ""} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!resolved && (
        <ApprovalBar
          onAccept={() => onResolve(proposal.id, true)}
          onReject={() => onResolve(proposal.id, false)}
        />
      )}
    </div>
  );
}
