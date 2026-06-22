import { useEffect } from "react";
import { AppHeader } from "./AppHeader";
import { Nav } from "./Nav";
import { PanelHost } from "./PanelHost";
import { FileDrawer } from "@/components/files/FileDrawer";
import { useBreakpoint } from "@/lib/breakpoint";
import { useGithubStore } from "@/state/github";

export function IdeShell() {
  const bp = useBreakpoint();
  const mobile = bp === "sm";
  const status = useGithubStore((s) => s.status);

  useEffect(() => {
    void status();
  }, [status]);

  return (
    <div className="flex h-full flex-col">
      <AppHeader />
      <div className="flex min-h-0 flex-1">
        {!mobile && <Nav orientation="vertical" />}
        <main className="min-h-0 min-w-0 flex-1">
          <PanelHost />
        </main>
      </div>
      {mobile && <Nav orientation="horizontal" />}
      {mobile && <FileDrawer />}
    </div>
  );
}
