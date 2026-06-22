import { useUiStore } from "@/state/ui";
import { useGithubStore } from "@/state/github";
import { ConnectGitHub } from "@/auth/ConnectGitHub";
import { RepoPicker } from "@/auth/RepoPicker";
import { FilesPanel } from "@/panels/FilesPanel";
import { EditorPanel } from "@/panels/EditorPanel";
import { EnaPanel } from "@/panels/EnaPanel";
import { ScmPanel } from "@/panels/ScmPanel";
import { TerminalPanel } from "@/panels/TerminalPanel";
import { PreviewPanel } from "@/panels/PreviewPanel";

export function PanelHost() {
  const { activePanel, scmOpen } = useUiStore();
  const { connected, currentRepo, loading } = useGithubStore();

  if (loading) {
    return (
      <div className="grid h-full place-items-center text-muted">
        <div className="h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }

  if (!connected) return <ConnectGitHub />;
  if (!currentRepo) return <RepoPicker />;

  return (
    <>
      {(() => {
        switch (activePanel) {
          case "files":
            return <FilesPanel />;
          case "editor":
            return <EditorPanel />;
          case "ena":
            return <EnaPanel />;
          case "terminal":
            return <TerminalPanel />;
          case "preview":
            return <PreviewPanel />;
          default:
            return <EditorPanel />;
        }
      })()}
      {scmOpen && <ScmPanel />}
    </>
  );
}
