import { AppHeader } from "./AppHeader";
import { Nav } from "./Nav";
import { PanelHost } from "./PanelHost";
import { useBreakpoint } from "@/lib/breakpoint";

export function IdeShell() {
  const bp = useBreakpoint();
  const mobile = bp === "sm";

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
    </div>
  );
}
