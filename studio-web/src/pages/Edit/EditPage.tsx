import * as React from "react";
import { ProjectTree } from "../../components/ProjectTree/ProjectTree";
import MonacoHost from "../../components/Editor/MonacoHost";
import { Diagnostics } from "../../components/Editor/Diagnostics";
import Toolbar from "../../components/Editor/Toolbar";

import CompilePanel from "./Panels/CompilePanel";
import SimulatePanel from "./Panels/SimulatePanel";
import EventsPanel from "./Panels/EventsPanel";
import ArtifactsPanel from "./Panels/ArtifactsPanel";
import DaPanel from "./Panels/DaPanel";

import { useProjectStore } from "../../state/project";
import { useCompileStore } from "../../state/compile";
import { useSimulateStore } from "../../state/simulate";
import { useToasts } from "../../state/toasts";
import { loadTemplateById, ensureDefaultTemplate } from "../../services/templates";
import { cx } from "../../utils/classnames";
import { formatAddress } from "../../utils/format";
import type { ProjectFile } from "../../types";

/** Simple local tabs for the right-side panel */
type RightTab = "compile" | "simulate" | "events" | "artifacts" | "da";

const RightTabButton: React.FC<{
  id: RightTab;
  active: RightTab;
  onSelect: (t: RightTab) => void;
  children: React.ReactNode;
}> = ({ id, active, onSelect, children }) => (
  <button
    type="button"
    onClick={() => onSelect(id)}
    className={cx(
      "px-3 py-2 text-sm font-medium border-b-2 -mb-px",
      active === id
        ? "border-[var(--accent,#0284c7)] text-[color:var(--accent,#0284c7)]"
        : "border-transparent text-[color:var(--muted,#6b7280)] hover:text-[color:var(--fg,#111827)] hover:border-[color:var(--divider,#e5e7eb)]"
    )}
  >
    {children}
  </button>
);

export default function EditPage() {
  const { pushToast } = useToasts();

  // Project state
  const files = useProjectStore((s) => s.files);
  const activePath = useProjectStore((s) => s.activePath);
  const setActive = useProjectStore((s) => s.setActive);
  const updateFile = useProjectStore((s) => s.updateFile);
  const removePath = useProjectStore((s) => s.removePath);
  const createFile = useProjectStore((s) => s.createFile);
  const isDirty = useProjectStore((s) => s.isDirty);
  const saveProject = useProjectStore((s) => s.saveToLocal);
  const loadProject = useProjectStore((s) => s.loadFromLocal);

  // Compile/simulate state
  const compiling = useCompileStore((s) => s.status === "running");
  const compile = useCompileStore((s) => s.compile);
  const lastGas = useCompileStore((s) => s.gasEstimate);
  const clearDiagnostics = useCompileStore((s) => s.clear);

  const simRunning = useSimulateStore((s) => s.isRunning);
  const stopSim = useSimulateStore((s) => s.stop);
  const runLast = useSimulateStore((s) => s.runLastConfigured); // may be undefined if not configured

  // UI local state (resizable panes, selected right tab)
  const [leftW, setLeftW] = React.useState<number>(280);
  const [rightW, setRightW] = React.useState<number>(360);
  const [rightTab, setRightTab] = React.useState<RightTab>("compile");

  // Load default template on first mount if project is empty
  React.useEffect(() => {
    if (Object.keys(files).length === 0) {
      // Try to restore last project, else load default "counter"
      const restored = loadProject();
      if (!restored) {
        ensureDefaultTemplate()
          .then((tpl) => useTemplate(tpl.id))
          .catch(() => void 0);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const useTemplate = async (id: string) => {
    try {
      const tpl = await loadTemplateById(id);
      tpl.files.forEach((f: ProjectFile) => {
        createFile(f.path, f.content);
      });
      if (tpl.entry) setActive(tpl.entry);
      pushToast({ kind: "success", title: `Loaded template: ${tpl.name}` });
    } catch (err: any) {
      pushToast({ kind: "error", title: "Template failed", description: String(err?.message || err) });
    }
  };

  // Toolbar handlers
  const handleRun = async () => {
    try {
      await compile();
      // If user has a last simulation configured, run it; otherwise just keep compile results.
      if (runLast) await runLast();
      setRightTab(runLast ? "simulate" : "compile");
    } catch (err: any) {
      setRightTab("compile");
      pushToast({ kind: "error", title: "Compile/Run failed", description: String(err?.message || err) });
    }
  };
  const handleStop = async () => {
    try {
      await stopSim();
    } catch {
      // swallow
    }
  };
  const handleSave = () => {
    const ok = saveProject();
    pushToast({
      kind: ok ? "success" : "info",
      title: ok ? "Project saved" : "Nothing to save",
      description: ok ? "Saved to local storage." : undefined,
    });
  };
  const handleFormat = () => {
    // Let MonacoHost handle this custom event (it listens and formats active doc)
    window.dispatchEvent(new CustomEvent("editor.format"));
  };

  // Editor bindings
  const activeContent = activePath ? files[activePath]?.content ?? "" : "";
  const onChange = (code: string) => {
    if (activePath) {
      updateFile(activePath, code);
      clearDiagnostics();
    }
  };

  // Basic resizer drag handlers
  const startDrag = (which: "left" | "right", e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startLeft = leftW;
    const startRight = rightW;
    const container = document.getElementById("edit-layout")!;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      if (which === "left") {
        const next = Math.min(480, Math.max(200, startLeft + dx));
        setLeftW(next);
      } else {
        const total = container.getBoundingClientRect().width;
        const next = Math.min(560, Math.max(280, startRight - dx));
        // clamp so center area doesn't collapse
        const centerMin = 420;
        if (total - (leftW + next) < centerMin) return;
        setRightW(next);
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <Toolbar
        onRun={handleRun}
        onStop={simRunning ? handleStop : undefined}
        onSave={handleSave}
        onFormat={handleFormat}
        isRunning={Boolean(simRunning || compiling)}
        isDirty={isDirty}
        canRun={true}
        canSave={true}
        canFormat={true}
        rightSlot={
          <div className="flex items-center gap-4 text-xs text-[color:var(--muted,#6b7280)]">
            {lastGas != null && (
              <div title="Estimated gas for last compile">
                Gas ≈ <span className="font-semibold text-[color:var(--fg,#111827)]">{lastGas}</span>
              </div>
            )}
            {activePath && <div title="Active file path">{activePath}</div>}
          </div>
        }
      />

      {/* Body */}
      <div id="edit-layout" className="flex-1 min-h-0 flex">
        {/* Left: Project tree */}
        <aside
          className="border-r border-[color:var(--divider,#e5e7eb)] bg-[var(--panel-bg,#fafafa)] min-w-[200px] max-w-[520px]"
          style={{ width: leftW }}
        >
          <div className="h-full overflow-auto">
            <ProjectTree
              files={files}
              activePath={activePath}
              onSelect={(p) => setActive(p)}
              onCreate={(p, content) => createFile(p, content)}
              onDelete={(p) => removePath(p)}
            />
          </div>
        </aside>

        {/* Left resizer */}
        <div
          onMouseDown={(e) => startDrag("left", e)}
          className="w-1 cursor-col-resize bg-transparent hover:bg-[color:var(--divider,#e5e7eb)]"
          title="Drag to resize"
          aria-hidden
        />

        {/* Center: Editor */}
        <main className="flex-1 min-w-0 flex flex-col">
          <div className="flex-1 min-h-0">
            <MonacoHost
              language={guessLanguage(activePath)}
              value={activeContent}
              onChange={onChange}
              path={activePath || "untitled.py"}
            />
          </div>
          {/* Diagnostics below editor */}
          <div className="border-t border-[color:var(--divider,#e5e7eb)]">
            <Diagnostics />
          </div>
        </main>

        {/* Right resizer */}
        <div
          onMouseDown={(e) => startDrag("right", e)}
          className="w-1 cursor-col-resize bg-transparent hover:bg-[color:var(--divider,#e5e7eb)]"
          title="Drag to resize"
          aria-hidden
        />

        {/* Right: Panels */}
        <aside
          className="border-l border-[color:var(--divider,#e5e7eb)] bg-[var(--panel-bg,#fafafa)] min-w-[280px] max-w-[600px] flex flex-col"
          style={{ width: rightW }}
        >
          {/* Tabs */}
          <div className="flex items-end px-2">
            <RightTabButton id="compile" active={rightTab} onSelect={setRightTab}>
              Compile
            </RightTabButton>
            <RightTabButton id="simulate" active={rightTab} onSelect={setRightTab}>
              Simulate
            </RightTabButton>
            <RightTabButton id="events" active={rightTab} onSelect={setRightTab}>
              Events
            </RightTabButton>
            <RightTabButton id="artifacts" active={rightTab} onSelect={setRightTab}>
              Artifacts
            </RightTabButton>
            <RightTabButton id="da" active={rightTab} onSelect={setRightTab}>
              DA
            </RightTabButton>
            <div className="flex-1" />
          </div>

          {/* Panel body */}
          <div className="flex-1 min-h-0 overflow-auto">
            {rightTab === "compile" && <CompilePanel />}
            {rightTab === "simulate" && <SimulatePanel />}
            {rightTab === "events" && <EventsPanel />}
            {rightTab === "artifacts" && <ArtifactsPanel />}
            {rightTab === "da" && <DaPanel />}
          </div>
        </aside>
      </div>
    </div>
  );
}

/** Heuristics for Monaco language from path */
function guessLanguage(path?: string | null) {
  if (!path) return "python";
  const p = path.toLowerCase();
  if (p.endsWith(".py")) return "python";
  if (p.endsWith(".json")) return "json";
  if (p.endsWith(".ir")) return "plaintext";
  if (p.endsWith(".md")) return "markdown";
  return "python";
}
